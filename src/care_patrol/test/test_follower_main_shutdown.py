"""Exercise the unmodified waypoint follower entry point under POSIX signals."""

import os
from itertools import count
from pathlib import Path
import signal
import subprocess
import threading
from time import monotonic, sleep

from geometry_msgs.msg import TransformStamped, Twist
from nav2_msgs.action import NavigateToPose
import pytest
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool
from std_srvs.srv import SetBool
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster


RUN_SHUTDOWN = os.environ.get('CARE_PATROL_FOLLOWER_MAIN_SHUTDOWN') == '1'
DOMAIN_IDS = count(181)

pytestmark = pytest.mark.skipif(
    not RUN_SHUTDOWN,
    reason='set CARE_PATROL_FOLLOWER_MAIN_SHUTDOWN=1 with the archive overlay',
)


class MainShutdownHarness(Node):
    """Provide mock Nav2 and observe the follower without publishing stop commands."""

    def __init__(self, *, accept_cancel):
        super().__init__('follower_main_shutdown_harness')
        self.accept_cancel = accept_cancel
        self.goals_received = 0
        self.cancel_requests = 0
        self.canceled_results = 0
        self.stop_values = []
        self.zero_twists = []
        self._closing = threading.Event()
        self.execution_finished = threading.Event()
        self._callback_group = ReentrantCallbackGroup()
        self.action_server = ActionServer(
            self,
            NavigateToPose,
            '/navigate_to_pose',
            execute_callback=self._execute,
            goal_callback=self._goal,
            cancel_callback=self._cancel,
            callback_group=self._callback_group,
        )
        self.pause_client = self.create_client(
            SetBool, '/koko_waypoint_follower/set_paused'
        )
        self.create_subscription(Bool, '/stop', self._stop, 10)
        self.create_subscription(Twist, '/cmd_vel_direct', self._twist, 10)
        self.tf_broadcaster = StaticTransformBroadcaster(self)
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
        transform.header.frame_id = 'map'
        transform.child_frame_id = 'care_patrol_test/base_footprint'
        transform.transform.rotation.w = 1.0
        self.tf_broadcaster.sendTransform(transform)

    def _goal(self, _request):
        self.goals_received += 1
        return GoalResponse.ACCEPT

    def _cancel(self, _goal_handle):
        self.cancel_requests += 1
        return CancelResponse.ACCEPT if self.accept_cancel else CancelResponse.REJECT

    def _execute(self, goal_handle):
        try:
            while rclpy.ok() and not self._closing.is_set():
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    self.canceled_results += 1
                    return NavigateToPose.Result()
                sleep(0.01)
            if goal_handle.is_active:
                goal_handle.abort()
            return NavigateToPose.Result()
        finally:
            self.execution_finished.set()

    def _stop(self, message):
        self.stop_values.append((monotonic(), bool(message.data)))

    def _twist(self, message):
        values = (
            message.linear.x,
            message.linear.y,
            message.linear.z,
            message.angular.x,
            message.angular.y,
            message.angular.z,
        )
        self.zero_twists.append((monotonic(), values))

    def close(self):
        self._closing.set()


def wait_until(predicate, timeout, message):
    """Wait for ROS state without hiding timeout failures."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.02)
    pytest.fail(message)


def run_manual_pause_signal(signum, *, accept_cancel):
    """Start the real entry point, pause it, signal it, and return observations."""
    executable = Path(os.environ['CARE_PATROL_FOLLOWER_EXECUTABLE']).resolve()
    waypoint_file = (
        Path(__file__).parent / 'fixtures' / 'integration_waypoints.yaml'
    ).resolve()
    assert executable.is_file()
    assert os.access(executable, os.X_OK)

    domain_id = next(DOMAIN_IDS)
    rclpy.init(domain_id=domain_id)
    harness = MainShutdownHarness(accept_cancel=accept_cancel)
    executor = MultiThreadedExecutor(num_threads=6)
    executor.add_node(harness)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    process_environment = os.environ.copy()
    process_environment['ROS_DOMAIN_ID'] = str(domain_id)
    process_environment['ROS_AUTOMATIC_DISCOVERY_RANGE'] = 'LOCALHOST'
    process = subprocess.Popen(
        [
            str(executable),
            '--ros-args',
            '-p', f'waypoint_file:={waypoint_file}',
            '-p', 'navigation_mode:=to_pose',
            '-p', 'base_frame:=care_patrol_test/base_footprint',
            '-p', 'person_area_cancel_timeout_sec:=5.0',
        ],
        cwd='/home/inoue-yuki/care_patrol_ws',
        env=process_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = ''
    try:
        wait_until(
            lambda: harness.goals_received == 1,
            5.0,
            'follower main did not send its initial Nav2 goal',
        )
        # The follower source publishes one VOLATILE /stop=false from its
        # constructor.  Record whether DDS discovery made that one-shot sample
        # externally visible, but do not mistake a discovery race for a failure
        # of the later signal-cleanup path exercised by this test.
        sleep(0.2)
        startup_stop = [value for _stamp, value in harness.stop_values]
        wait_until(
            harness.pause_client.service_is_ready,
            5.0,
            'follower main did not expose set_paused',
        )
        request = SetBool.Request()
        request.data = True
        future = harness.pause_client.call_async(request)
        wait_until(future.done, 3.0, 'set_paused did not respond')
        response = future.result()
        assert response.success
        wait_until(
            lambda: harness.cancel_requests == 1,
            3.0,
            'manual pause did not request Nav2 cancellation',
        )
        wait_until(
            lambda: any(value for _stamp, value in harness.stop_values),
            3.0,
            'manual pause did not publish /stop=true',
        )
        if accept_cancel:
            wait_until(
                lambda: harness.canceled_results == 1,
                3.0,
                'mock Nav2 did not complete cancellation',
            )

        signal_sent_at = monotonic()
        stop_index = len(harness.stop_values)
        process.send_signal(signum)
        output, _unused = process.communicate(timeout=6.0)
        sleep(0.2)
        after_signal = harness.stop_values[stop_index:]
        result = {
            'signum': signum,
            'returncode': process.returncode,
            'signal_to_exit_sec': monotonic() - signal_sent_at,
            'cancel_requests': harness.cancel_requests,
            'canceled_results': harness.canceled_results,
            'stop_before_pause': startup_stop,
            'stop_after_signal': [value for _stamp, value in after_signal],
            'last_stop': harness.stop_values[-1][1],
            'zero_count': len(harness.zero_twists),
            'output': output,
        }
        print(f'FOLLOWER_MAIN_RESULT={result}')
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                output, _unused = process.communicate(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _unused = process.communicate(timeout=3.0)
        harness.close()
        if harness.goals_received:
            assert harness.execution_finished.wait(timeout=2.0)
        executor.remove_node(harness)
        harness.destroy_node()
        executor.shutdown(timeout_sec=3.0)
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=3.0)
        assert not spin_thread.is_alive()


@pytest.mark.parametrize(
    ('signum', 'expected_returncode'),
    ((signal.SIGINT, 130), (signal.SIGTERM, 143)),
)
def test_manual_pause_signal_releases_lock_after_confirmed_cancel(
    signum, expected_returncode
):
    """Confirmed cleanup releases the software stop lock before a signal exit."""
    result = run_manual_pause_signal(signum, accept_cancel=True)
    assert result['returncode'] == expected_returncode
    assert result['cancel_requests'] == 1
    assert result['canceled_results'] == 1
    assert False in result['stop_after_signal']
    assert result['last_stop'] is False
    assert result['zero_count'] > 0
    assert 'Shutdown unconfirmed' not in result['output']


def test_manual_pause_sigterm_keeps_lock_when_cancel_is_unconfirmed():
    """Unresolved Nav2 ownership must keep `/stop=true` and return failure."""
    result = run_manual_pause_signal(signal.SIGTERM, accept_cancel=False)
    assert result['returncode'] == 2
    assert result['cancel_requests'] == 1
    assert result['canceled_results'] == 0
    assert False not in result['stop_after_signal']
    assert result['last_stop'] is True
    assert result['zero_count'] > 0
    assert 'Shutdown unconfirmed' in result['output']
