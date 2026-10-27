import unittest
from pathlib import Path
import tempfile

from care_patrol.person_stop_guard import (
    AdvisoryCameraLock,
    CameraOwnershipError,
    ConsecutivePersonGate,
    OpenCvFrameSource,
    StopConfirmationTracker,
)


class TestConsecutivePersonGate(unittest.TestCase):

    def test_requires_consecutive_frames_and_latches_once(self):
        gate = ConsecutivePersonGate(3)

        self.assertFalse(gate.observe(True))
        self.assertFalse(gate.observe(False))
        self.assertFalse(gate.observe(True))
        self.assertFalse(gate.observe(True))
        self.assertTrue(gate.observe(True))
        self.assertFalse(gate.observe(True))
        self.assertTrue(gate.latched)


class TestStopConfirmationTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = StopConfirmationTracker(
            require_odometry=True,
            stationary_samples=3,
            linear_threshold=0.03,
            angular_threshold=0.05,
        )
        self.tracker.mark_request_dispatched()

    def test_service_acceptance_alone_is_unconfirmed(self):
        self.tracker.mark_service_accepted()

        self.assertFalse(self.tracker.confirmed)

    def test_pre_acceptance_observations_are_discarded(self):
        self.tracker.observe_stop_hold(True)
        for _ in range(3):
            self.tracker.observe_odometry(0.0, 0.0)

        self.tracker.mark_service_accepted()

        self.assertFalse(self.tracker.stop_hold_observed)
        self.assertEqual(self.tracker.stationary_count, 0)
        self.assertFalse(self.tracker.confirmed)

    def test_stop_hold_without_stationary_odometry_is_unconfirmed(self):
        self.tracker.mark_service_accepted()
        self.tracker.observe_stop_hold(True)
        self.tracker.observe_odometry(0.10, 0.0)

        self.assertFalse(self.tracker.confirmed)

    def test_stationary_odometry_without_stop_hold_is_unconfirmed(self):
        self.tracker.mark_service_accepted()
        for _ in range(3):
            self.tracker.observe_odometry(0.0, 0.0)

        self.assertFalse(self.tracker.confirmed)

    def test_stop_hold_and_consecutive_stationary_odometry_confirms(self):
        self.tracker.mark_service_accepted()
        self.tracker.observe_stop_hold(True)
        for _ in range(3):
            self.tracker.observe_odometry(0.01, 0.01)

        self.assertTrue(self.tracker.confirmed)

    def test_motion_resets_stationary_sample_count(self):
        self.tracker.mark_service_accepted()
        self.tracker.observe_stop_hold(True)
        self.tracker.observe_odometry(0.0, 0.0)
        self.tracker.observe_odometry(0.0, 0.0)
        self.tracker.observe_odometry(0.2, 0.0)
        self.tracker.observe_odometry(0.0, 0.0)

        self.assertFalse(self.tracker.confirmed)


class TestCameraOwnership(unittest.TestCase):

    def test_direct_camera_requires_explicit_standalone_permission(self):
        with self.assertRaises(CameraOwnershipError):
            OpenCvFrameSource(
                input_mode='camera',
                video_path='',
                camera_device='/dev/video-test',
                allow_direct_camera=False,
                refuse_if_camera_v7_running=False,
                camera_lock_path='',
                camera_width=640,
                camera_height=480,
            )

    def test_advisory_lock_rejects_second_cooperating_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / 'camera.lock')
            first = AdvisoryCameraLock('/dev/video-test', lock_path)
            second = AdvisoryCameraLock('/dev/video-test', lock_path)
            first.acquire()
            try:
                with self.assertRaises(CameraOwnershipError):
                    second.acquire()
            finally:
                second.release()
                first.release()

    def test_missing_camera_device_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'failed to open camera input'):
                OpenCvFrameSource(
                    input_mode='camera',
                    video_path='',
                    camera_device='/dev/v4l/by-id/care-patrol-missing-camera',
                    allow_direct_camera=True,
                    refuse_if_camera_v7_running=False,
                    camera_lock_path=str(Path(directory) / 'missing-camera.lock'),
                    camera_width=640,
                    camera_height=480,
                )


if __name__ == '__main__':
    unittest.main()
