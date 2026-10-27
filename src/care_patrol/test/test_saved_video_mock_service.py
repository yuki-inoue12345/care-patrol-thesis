import os
from pathlib import Path
import tempfile
from time import monotonic, sleep
import unittest

import cv2
import numpy as np
from nav_msgs.msg import Odometry
import rclpy
from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import Bool
from std_srvs.srv import SetBool

from care_patrol.person_stop_guard import (
    DetectionObservation,
    PersonStopGuard,
    StopState,
    YoloPersonDetector,
)


class AlwaysPersonDetector:

    def detect(self, _frame):
        return DetectionObservation(True, max_confidence=0.90, count=1)


class MockPauseService(Node):

    def __init__(
        self, service_name, stop_topic, odometry_topic, *, publish_confirmation=True
    ):
        super().__init__('mock_pause_service')
        self.requests = []
        self.publish_confirmation = publish_confirmation
        self.confirmation_publishes_remaining = 0
        self.stop_publisher = self.create_publisher(Bool, stop_topic, 10)
        self.odometry_publisher = self.create_publisher(
            Odometry, odometry_topic, 10
        )
        self.service = self.create_service(SetBool, service_name, self._handle)
        self.confirmation_timer = self.create_timer(0.05, self._publish_confirmation)

    def _handle(self, request, response):
        self.requests.append(bool(request.data))
        response.success = True
        response.message = 'mock pause accepted'
        if self.publish_confirmation:
            self.confirmation_publishes_remaining = 4
        return response

    def publish_stale_confirmation(self):
        self.stop_publisher.publish(Bool(data=True))
        for _ in range(4):
            self.odometry_publisher.publish(Odometry())

    def _publish_confirmation(self):
        if self.confirmation_publishes_remaining <= 0:
            return
        self.stop_publisher.publish(Bool(data=True))
        self.odometry_publisher.publish(Odometry())
        self.confirmation_publishes_remaining -= 1


class SlowPauseService(Node):
    """Accept one request only after the guard's response deadline."""

    def __init__(self, service_name, delay_sec):
        super().__init__('slow_mock_pause_service')
        self.requests = []
        self.delay_sec = delay_sec
        self.service = self.create_service(SetBool, service_name, self._handle)

    def _handle(self, request, response):
        self.requests.append(bool(request.data))
        sleep(self.delay_sec)
        response.success = True
        response.message = 'late mock response; no physical stop'
        return response


class RejectingPauseService(Node):
    """Return a completed SetBool response with success=false."""

    def __init__(self, service_name):
        super().__init__('rejecting_mock_pause_service')
        self.requests = []
        self.service = self.create_service(SetBool, service_name, self._handle)

    def _handle(self, request, response):
        self.requests.append(bool(request.data))
        response.success = False
        response.message = 'mock rejection; no stop performed'
        return response


def make_saved_video(path):
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*'MJPG'), 10.0, (320, 240)
    )
    if not writer.isOpened():
        raise RuntimeError('test video writer could not be opened')
    for index in range(12):
        frame = np.full((240, 320, 3), 40 + index, dtype=np.uint8)
        writer.write(frame)
    writer.release()


class TestSavedVideoWithMockPauseService(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        rclpy.init()

    @classmethod
    def tearDownClass(cls):
        if rclpy.ok():
            rclpy.shutdown()

    def test_one_pause_request_then_confirmed_stop(self):
        integration_video = os.environ.get('CARE_PATROL_TEST_VIDEO', '')
        integration_model = os.environ.get('CARE_PATROL_TEST_MODEL', '')
        with tempfile.TemporaryDirectory() as directory:
            if integration_video and integration_model:
                video_path = Path(integration_video)
                detector = YoloPersonDetector(integration_model, 0.35, 640)
                timeout = 30.0
            else:
                video_path = Path(directory) / 'person_input.avi'
                make_saved_video(video_path)
                detector = AlwaysPersonDetector()
                timeout = 10.0

            service_name = '/care_patrol_test/set_paused'
            stop_topic = '/care_patrol_test/stop'
            odometry_topic = '/care_patrol_test/odom'
            mock = MockPauseService(service_name, stop_topic, odometry_topic)
            guard = PersonStopGuard(
                detector=detector,
                parameter_overrides=[
                    Parameter('input_mode', value='video'),
                    Parameter('video_path', value=str(video_path)),
                    Parameter('required_consecutive_frames', value=3),
                    Parameter('processing_rate_hz', value=20.0),
                    Parameter('pause_service', value=service_name),
                    Parameter('stop_confirmation_topic', value=stop_topic),
                    Parameter('odometry_topic', value=odometry_topic),
                    Parameter('service_wait_timeout_sec', value=3.0),
                    Parameter('service_response_timeout_sec', value=3.0),
                    Parameter('stop_confirmation_timeout_sec', value=3.0),
                    Parameter('stationary_samples', value=3),
                ],
            )
            executor = SingleThreadedExecutor()
            executor.add_node(mock)
            executor.add_node(guard)
            try:
                deadline = monotonic() + timeout
                while monotonic() < deadline and guard.state is not StopState.STOP_CONFIRMED:
                    executor.spin_once(timeout_sec=0.05)

                self.assertEqual(guard.state, StopState.STOP_CONFIRMED)
                self.assertEqual(mock.requests, [True])

                duplicate_check_deadline = monotonic() + 0.5
                while monotonic() < duplicate_check_deadline:
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(mock.requests, [True])
                self.assertEqual(guard.state, StopState.STOP_CONFIRMED)
            finally:
                executor.remove_node(guard)
                executor.remove_node(mock)
                guard.destroy_node()
                mock.destroy_node()
                executor.shutdown()

    def test_service_acceptance_stays_unconfirmed_without_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / 'person_input.avi'
            make_saved_video(video_path)
            service_name = '/care_patrol_test_unconfirmed/set_paused'
            stop_topic = '/care_patrol_test_unconfirmed/stop'
            odometry_topic = '/care_patrol_test_unconfirmed/odom'
            mock = MockPauseService(
                service_name,
                stop_topic,
                odometry_topic,
                publish_confirmation=False,
            )
            guard = PersonStopGuard(
                detector=AlwaysPersonDetector(),
                parameter_overrides=[
                    Parameter('input_mode', value='video'),
                    Parameter('video_path', value=str(video_path)),
                    Parameter('required_consecutive_frames', value=2),
                    Parameter('processing_rate_hz', value=20.0),
                    Parameter('pause_service', value=service_name),
                    Parameter('stop_confirmation_topic', value=stop_topic),
                    Parameter('odometry_topic', value=odometry_topic),
                    Parameter('stop_confirmation_timeout_sec', value=0.2),
                ],
            )
            executor = SingleThreadedExecutor()
            executor.add_node(mock)
            executor.add_node(guard)
            try:
                # These messages precede the successful service response and
                # must not be reused as evidence for the new stop request.
                mock.publish_stale_confirmation()
                deadline = monotonic() + 3.0
                while (
                    monotonic() < deadline
                    and guard.state is not StopState.REQUEST_ACCEPTED_UNCONFIRMED
                ):
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(
                    guard.state, StopState.REQUEST_ACCEPTED_UNCONFIRMED
                )

                timeout_deadline = monotonic() + 0.5
                while monotonic() < timeout_deadline:
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(
                    guard.state, StopState.REQUEST_ACCEPTED_UNCONFIRMED
                )
                self.assertEqual(mock.requests, [True])
            finally:
                executor.remove_node(guard)
                executor.remove_node(mock)
                guard.destroy_node()
                mock.destroy_node()
                executor.shutdown()

    def test_missing_service_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / 'person_input.avi'
            make_saved_video(video_path)
            guard = PersonStopGuard(
                detector=AlwaysPersonDetector(),
                parameter_overrides=[
                    Parameter('input_mode', value='video'),
                    Parameter('video_path', value=str(video_path)),
                    Parameter('required_consecutive_frames', value=1),
                    Parameter('processing_rate_hz', value=20.0),
                    Parameter(
                        'pause_service',
                        value='/care_patrol_test_missing/set_paused',
                    ),
                    Parameter('service_wait_timeout_sec', value=0.2),
                ],
            )
            executor = SingleThreadedExecutor()
            executor.add_node(guard)
            try:
                deadline = monotonic() + 1.0
                while (
                    monotonic() < deadline
                    and guard.state is not StopState.SERVICE_UNAVAILABLE
                ):
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(guard.state, StopState.SERVICE_UNAVAILABLE)
                self.assertFalse(guard._confirmation.request_dispatched)
                self.assertFalse(guard._confirmation.service_accepted)
            finally:
                executor.remove_node(guard)
                guard.destroy_node()
                executor.shutdown()

    def test_explicit_service_rejection_is_not_retried_or_confirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / 'person_input.avi'
            make_saved_video(video_path)
            service_name = '/care_patrol_test_rejected/set_paused'
            mock = RejectingPauseService(service_name)
            guard = PersonStopGuard(
                detector=AlwaysPersonDetector(),
                parameter_overrides=[
                    Parameter('input_mode', value='video'),
                    Parameter('video_path', value=str(video_path)),
                    Parameter('required_consecutive_frames', value=1),
                    Parameter('processing_rate_hz', value=20.0),
                    Parameter('pause_service', value=service_name),
                    Parameter('service_wait_timeout_sec', value=0.5),
                    Parameter('service_response_timeout_sec', value=0.5),
                ],
            )
            executor = SingleThreadedExecutor()
            executor.add_node(mock)
            executor.add_node(guard)
            try:
                deadline = monotonic() + 2.0
                while (
                    monotonic() < deadline
                    and guard.state is not StopState.REQUEST_REJECTED
                ):
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(guard.state, StopState.REQUEST_REJECTED)
                self.assertEqual(mock.requests, [True])
                self.assertFalse(guard._confirmation.service_accepted)

                duplicate_check_deadline = monotonic() + 0.3
                while monotonic() < duplicate_check_deadline:
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(mock.requests, [True])
                self.assertNotEqual(guard.state, StopState.STOP_CONFIRMED)
            finally:
                executor.remove_node(guard)
                executor.remove_node(mock)
                guard.destroy_node()
                mock.destroy_node()
                executor.shutdown()

    def test_late_service_response_is_not_retried_or_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / 'person_input.avi'
            make_saved_video(video_path)
            service_name = '/care_patrol_test_slow/set_paused'
            mock = SlowPauseService(service_name, delay_sec=0.5)
            guard = PersonStopGuard(
                detector=AlwaysPersonDetector(),
                parameter_overrides=[
                    Parameter('input_mode', value='video'),
                    Parameter('video_path', value=str(video_path)),
                    Parameter('required_consecutive_frames', value=1),
                    Parameter('processing_rate_hz', value=20.0),
                    Parameter('pause_service', value=service_name),
                    Parameter('service_wait_timeout_sec', value=0.5),
                    Parameter('service_response_timeout_sec', value=0.15),
                ],
            )
            executor = MultiThreadedExecutor(num_threads=3)
            executor.add_node(mock)
            executor.add_node(guard)
            try:
                deadline = monotonic() + 1.0
                while monotonic() < deadline:
                    executor.spin_once(timeout_sec=0.05)
                self.assertEqual(
                    guard.state, StopState.REQUEST_OUTCOME_UNKNOWN
                )
                self.assertEqual(mock.requests, [True])
                self.assertTrue(guard._confirmation.request_dispatched)
                self.assertFalse(guard._confirmation.service_accepted)
            finally:
                executor.remove_node(guard)
                executor.remove_node(mock)
                guard.destroy_node()
                mock.destroy_node()
                executor.shutdown()


if __name__ == '__main__':
    unittest.main()
