"""Isolated integration with the unmodified 2026-10-25 waypoint follower."""

import os
from pathlib import Path
import threading
import tempfile
from time import monotonic, sleep

import cv2
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from nav2_msgs.action import NavigateToPose
import numpy as np
import pytest
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_services_default
from rclpy.service_introspection import ServiceIntrospectionState
from service_msgs.msg import ServiceEventInfo
from std_msgs.msg import Bool
from std_srvs.srv import SetBool
from tf2_ros.static_transform_broadcaster import StaticTransformBroadcaster

from care_patrol.person_stop_guard import (
    DetectionObservation,
    PersonStopGuard,
    StopState,
    YoloPersonDetector,
)


RUN_INTEGRATION = os.environ.get('CARE_PATROL_REAL_WAYPOINT_INTEGRATION') == '1'

if RUN_INTEGRATION:
    from robotbase_waypoint.waypoint_follow import WaypointFollower
else:
    WaypointFollower = None


pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason='set CARE_PATROL_REAL_WAYPOINT_INTEGRATION=1 with the archive overlay',
)


class MeasuredDetector:
    """Record timing while delegating every inference to the real YOLO detector."""

    def __init__(self, detector):
        self.detector = detector
        self.calls = 0
        self.durations = []
        self.observations = []
        self.started_at = []
        self.completed_at = []

    def detect(self, frame):
        started = monotonic()
        observation = self.detector.detect(frame)
        completed = monotonic()
        self.started_at.append(started)
        self.completed_at.append(completed)
        self.durations.append(completed - started)
        self.observations.append(observation)
        self.calls += 1
        return observation


class AlwaysPersonDetector:
    """Deterministic person input for transport failure-path tests."""

    def detect(self, _frame):
        return DetectionObservation(True, max_confidence=0.90, count=1)


class EndlessFrameSource:
    """Supply frames until the guard latches, without camera dependencies."""

    def read(self):
        return True, object()

    def close(self):
        return None


class MockNavigateToPose(Node):
    """Accept one Nav2 goal, remain active, and honor or reject cancellation."""

    def __init__(self, *, accept_cancel=True):
        super().__init__('stage2_mock_navigate_to_pose')
        self.accept_cancel = accept_cancel
        self.goals_received = 0
        self.cancel_requests = 0
        self.canceled_results = 0
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
        self.odom_publisher = self.create_publisher(
            Odometry, '/care_patrol_test/odom', 10
        )
        self.odom_timer = self.create_timer(
            0.05, self._publish_odometry, callback_group=self._callback_group
        )
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

    def _publish_odometry(self):
        message = Odometry()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = 'odom'
        message.child_frame_id = 'care_patrol_test/base_footprint'
        if not self.accept_cancel or self.cancel_requests == 0:
            message.twist.twist.linear.x = 0.20
        self.odom_publisher.publish(message)

    def close(self):
        self._closing.set()


class IntegrationObserver(Node):
    """Observe only; never publish stop or velocity commands."""

    def __init__(self):
        super().__init__('care_patrol_integration_observer')
        self.pause_requests = []
        self.stop_values = []
        self.zero_twists = []
        self.create_subscription(Bool, '/stop', self._stop, 10)
        self.create_subscription(Twist, '/cmd_vel_direct', self._twist, 10)
        self.create_subscription(
            SetBool.Event,
            '/koko_waypoint_follower/set_paused/_service_event',
            self._service_event,
            qos_profile_services_default,
        )

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

    def _service_event(self, message):
        if message.info.event_type != ServiceEventInfo.REQUEST_RECEIVED:
            return
        self.pause_requests.extend(bool(request.data) for request in message.request)


def wait_until(predicate, timeout, message):
    """Wait with a clear integration failure instead of an unbounded spin."""
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.02)
    pytest.fail(message)


def video_metadata(path):
    """Read metadata without changing the input video."""
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f'could not inspect video: {path}')
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    return fps, frames


def create_blank_video(path, frames=20):
    """Create an explicit no-person saved-video input."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10.0, (320, 240)
    )
    if not writer.isOpened():
        raise RuntimeError('blank integration video writer could not be opened')
    try:
        for _index in range(frames):
            writer.write(np.full((240, 320, 3), 32, dtype=np.uint8))
    finally:
        writer.release()


def test_yolo_pauses_real_waypoint_follower_once():
    """YOLO must drive the real follower cancel-and-hold path exactly once."""
    model_path = Path(os.environ['CARE_PATROL_TEST_MODEL']).resolve()
    video_path = Path(os.environ['CARE_PATROL_TEST_VIDEO']).resolve()
    waypoint_path = Path(__file__).parent / 'fixtures' / 'integration_waypoints.yaml'
    assert model_path.is_file()
    assert video_path.is_file()
    assert waypoint_path.is_file()
    source_fps, source_frames = video_metadata(video_path)

    rclpy.init(args=[
        '--ros-args',
        '-p', f'waypoint_file:={waypoint_path}',
        '-p', 'navigation_mode:=to_pose',
        '-p', 'base_frame:=care_patrol_test/base_footprint',
        '-p', 'person_area_cancel_timeout_sec:=2.0',
    ])
    mock_nav = MockNavigateToPose()
    observer = IntegrationObserver()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(mock_nav)
    executor.add_node(observer)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    follower = None
    guard = None
    try:
        follower = WaypointFollower()
        follower.pause_service.configure_introspection(
            follower.get_clock(),
            qos_profile_services_default,
            ServiceIntrospectionState.CONTENTS,
        )
        follower.start()
        executor.add_node(follower)
        wait_until(
            lambda: mock_nav.goals_received == 1 and follower.goal_handle is not None,
            5.0,
            'the real waypoint follower did not enter an active Nav2 goal',
        )

        detector = MeasuredDetector(YoloPersonDetector(str(model_path), 0.5, 640))
        guard_started = monotonic()
        guard = PersonStopGuard(
            detector=detector,
            parameter_overrides=[
                Parameter('input_mode', value='video'),
                Parameter('video_path', value=str(video_path)),
                Parameter('required_consecutive_frames', value=3),
                Parameter('processing_rate_hz', value=10.0),
                Parameter(
                    'pause_service',
                    value='/koko_waypoint_follower/set_paused',
                ),
                Parameter('stop_confirmation_topic', value='/stop'),
                Parameter('odometry_topic', value='/care_patrol_test/odom'),
                Parameter('service_wait_timeout_sec', value=3.0),
                Parameter('service_response_timeout_sec', value=3.0),
                Parameter('stop_confirmation_timeout_sec', value=5.0),
                Parameter('stationary_samples', value=3),
            ],
        )
        executor.add_node(guard)
        wait_until(
            lambda: guard.state is StopState.STOP_CONFIRMED,
            15.0,
            f'guard did not confirm the simulated stop; state={guard.state}',
        )
        confirmed_at = monotonic()
        sleep(1.0)

        assert observer.pause_requests == [True]
        assert mock_nav.goals_received == 1
        assert mock_nav.cancel_requests == 1
        assert mock_nav.canceled_results == 1
        assert follower.manual_paused
        assert any(value for _stamp, value in observer.stop_values)
        assert observer.stop_values[-1][1] is True
        assert observer.zero_twists
        assert all(
            all(component == 0.0 for component in values)
            for _stamp, values in observer.zero_twists
        )
        assert all(record.resolved for record in follower.goal_records.values())
        assert guard.state is StopState.STOP_CONFIRMED
        assert detector.calls >= 3
        assert detector.observations[-1].detected

        elapsed = confirmed_at - guard_started
        detection_delay = guard._person_confirmed_at - guard_started
        first_positive_index = next(
            index
            for index, item in enumerate(detector.observations)
            if item.detected
        )
        first_detectable_frame_at = detector.started_at[first_positive_index]
        first_detection_result_at = detector.completed_at[first_positive_index]
        mean_inference = sum(detector.durations) / len(detector.durations)
        measured = {
            'source_fps': source_fps,
            'source_frames': source_frames,
            'configured_playback_fps': guard.processing_rate_hz,
            'frames_processed': detector.calls,
            'mean_inference_ms': mean_inference * 1000.0,
            'effective_inference_fps': 1.0 / mean_inference,
            'observed_processing_fps_to_latch': detector.calls / detection_delay,
            'guard_start_to_person_latch_sec': detection_delay,
            'first_detectable_frame_to_person_latch_sec': (
                guard._person_confirmed_at - first_detectable_frame_at
            ),
            'first_detection_result_to_person_latch_sec': (
                guard._person_confirmed_at - first_detection_result_at
            ),
            'first_detectable_frame_to_request_sec': (
                guard._request_sent_at - first_detectable_frame_at
            ),
            'first_detectable_frame_to_stop_confirmed_sec': (
                confirmed_at - first_detectable_frame_at
            ),
            'guard_start_to_stop_confirmed_sec': elapsed,
            'person_latch_to_request_sec': (
                guard._request_sent_at - guard._person_confirmed_at
            ),
            'request_to_stop_confirmed_sec': (
                confirmed_at - guard._request_sent_at
            ),
            'max_person_confidence': max(
                item.max_confidence for item in detector.observations
            ),
        }
        print(f'INTEGRATION_METRICS={measured}')

        executor.remove_node(guard)
        guard.destroy_node()
        guard = None
        sleep(0.2)
        assert observer.pause_requests == [True]
        assert mock_nav.goals_received == 1
        assert follower.manual_paused
        assert observer.stop_values[-1][1] is True
    finally:
        if guard is not None:
            executor.remove_node(guard)
            guard.destroy_node()
        mock_nav.close()
        assert mock_nav.execution_finished.wait(timeout=2.0)
        if follower is not None:
            deadline = monotonic() + 2.0
            while (
                monotonic() < deadline
                and not all(record.resolved for record in follower.goal_records.values())
            ):
                sleep(0.02)
            assert all(record.resolved for record in follower.goal_records.values())
        if follower is not None:
            executor.remove_node(follower)
            follower.destroy_node()
        executor.remove_node(observer)
        executor.remove_node(mock_nav)
        observer.destroy_node()
        mock_nav.destroy_node()
        executor.shutdown(timeout_sec=3.0)
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=3.0)

        assert not spin_thread.is_alive()


def test_cancel_rejection_never_reports_stop_confirmed():
    """Moving odometry must keep a rejected Nav2 cancel UNCONFIRMED."""
    waypoint_path = Path(__file__).parent / 'fixtures' / 'integration_waypoints.yaml'
    rclpy.init(args=[
        '--ros-args',
        '-p', f'waypoint_file:={waypoint_path}',
        '-p', 'navigation_mode:=to_pose',
        '-p', 'base_frame:=care_patrol_test/base_footprint',
        '-p', 'person_area_cancel_timeout_sec:=0.5',
    ])
    mock_nav = MockNavigateToPose(accept_cancel=False)
    observer = IntegrationObserver()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(mock_nav)
    executor.add_node(observer)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()
    follower = None
    guard = None
    try:
        follower = WaypointFollower()
        follower.pause_service.configure_introspection(
            follower.get_clock(),
            qos_profile_services_default,
            ServiceIntrospectionState.CONTENTS,
        )
        follower.start()
        executor.add_node(follower)
        wait_until(
            lambda: mock_nav.goals_received == 1 and follower.goal_handle is not None,
            5.0,
            'the real follower did not start its simulated Nav2 goal',
        )
        guard = PersonStopGuard(
            detector=AlwaysPersonDetector(),
            frame_source=EndlessFrameSource(),
            parameter_overrides=[
                Parameter('input_mode', value='video'),
                Parameter('video_path', value='/unused/injected-frame-source'),
                Parameter('required_consecutive_frames', value=1),
                Parameter('processing_rate_hz', value=20.0),
                Parameter(
                    'pause_service',
                    value='/koko_waypoint_follower/set_paused',
                ),
                Parameter('stop_confirmation_topic', value='/stop'),
                Parameter('odometry_topic', value='/care_patrol_test/odom'),
                Parameter('stop_confirmation_timeout_sec', value=0.6),
                Parameter('stationary_samples', value=3),
            ],
        )
        executor.add_node(guard)
        wait_until(
            lambda: mock_nav.cancel_requests == 1,
            5.0,
            'the real follower did not request Nav2 cancellation',
        )
        wait_until(
            lambda: any(value for _stamp, value in observer.stop_values),
            3.0,
            'the real follower did not engage its stop hold',
        )
        wait_until(
            lambda: follower.shutdown_request.requested,
            3.0,
            'the real follower did not flag unconfirmed cancellation',
        )
        sleep(0.4)

        assert observer.pause_requests == [True]
        assert mock_nav.goals_received == 1
        assert mock_nav.cancel_requests == 1
        assert mock_nav.canceled_results == 0
        assert follower.manual_paused
        assert guard.state is StopState.REQUEST_ACCEPTED_UNCONFIRMED
        assert not guard._confirmation.confirmed
        assert guard._confirmation.stationary_count == 0
    finally:
        if guard is not None:
            executor.remove_node(guard)
            guard.destroy_node()
        mock_nav.close()
        assert mock_nav.execution_finished.wait(timeout=2.0)
        if follower is not None:
            deadline = monotonic() + 2.0
            while (
                monotonic() < deadline
                and not all(record.resolved for record in follower.goal_records.values())
            ):
                sleep(0.02)
            assert all(record.resolved for record in follower.goal_records.values())
        if follower is not None:
            executor.remove_node(follower)
            follower.destroy_node()
        executor.remove_node(observer)
        executor.remove_node(mock_nav)
        observer.destroy_node()
        mock_nav.destroy_node()
        executor.shutdown(timeout_sec=3.0)
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=3.0)
        assert not spin_thread.is_alive()


def test_yolo_blank_saved_video_sends_no_request():
    """Actual YOLO inference on a blank video must end without a service call."""
    model_path = Path(os.environ['CARE_PATROL_TEST_MODEL']).resolve()
    with tempfile.TemporaryDirectory() as directory:
        video_path = Path(directory) / 'no_person.avi'
        create_blank_video(video_path)
        rclpy.init()
        detector = MeasuredDetector(YoloPersonDetector(str(model_path), 0.5, 640))
        guard = PersonStopGuard(
            detector=detector,
            parameter_overrides=[
                Parameter('input_mode', value='video'),
                Parameter('video_path', value=str(video_path)),
                Parameter('required_consecutive_frames', value=3),
                Parameter('processing_rate_hz', value=20.0),
                Parameter(
                    'pause_service',
                    value='/care_patrol_test/service_must_not_be_called',
                ),
                Parameter('service_wait_timeout_sec', value=0.5),
            ],
        )
        executor = SingleThreadedExecutor()
        executor.add_node(guard)
        try:
            deadline = monotonic() + 10.0
            while (
                monotonic() < deadline
                and guard.state is StopState.MONITORING
            ):
                executor.spin_once(timeout_sec=0.05)
            assert guard.state is StopState.INPUT_ENDED_NO_DETECTION
            assert detector.calls == 20
            assert not any(item.detected for item in detector.observations)
            assert not guard._confirmation.request_dispatched
            print(
                'NO_PERSON_METRICS='
                f'{{"frames_processed": {detector.calls}, '
                f'"mean_inference_ms": '
                f'{1000.0 * sum(detector.durations) / len(detector.durations):.3f}}}'
            )
        finally:
            executor.remove_node(guard)
            guard.destroy_node()
            executor.shutdown(timeout_sec=3.0)
            if rclpy.ok():
                rclpy.shutdown()
