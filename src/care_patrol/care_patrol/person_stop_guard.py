"""Detect people with YOLO and request the existing waypoint follower to pause.

The node deliberately owns neither Nav2 nor motor-stop logic.  It sends one
``SetBool(data=True)`` request and then observes the existing stop-hold signal
and odometry.  It never sends a resume request.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import monotonic
from typing import Any, Optional, Sequence

import cv2
from nav_msgs.msg import Odometry
import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)
from std_msgs.msg import Bool
from std_srvs.srv import SetBool


class StopState(str, Enum):
    """Externally meaningful states of a latched stop request."""

    MONITORING = 'monitoring'
    WAITING_FOR_SERVICE = 'waiting_for_service'
    REQUEST_SENT = 'request_sent'
    REQUEST_ACCEPTED_UNCONFIRMED = 'request_accepted_unconfirmed'
    STOP_CONFIRMED = 'stop_confirmed'
    SERVICE_UNAVAILABLE = 'service_unavailable'
    REQUEST_REJECTED = 'request_rejected'
    REQUEST_OUTCOME_UNKNOWN = 'request_outcome_unknown'
    INPUT_ENDED_NO_DETECTION = 'input_ended_no_detection'
    FAULT = 'fault'


@dataclass(frozen=True)
class DetectionObservation:
    """Person detections from one frame."""

    detected: bool
    max_confidence: float = 0.0
    count: int = 0


class ConsecutivePersonGate:
    """Latch after a configurable number of consecutive positive frames."""

    def __init__(self, required_frames: int) -> None:
        if required_frames < 1:
            raise ValueError('required_frames must be at least 1')
        self.required_frames = int(required_frames)
        self.consecutive_frames = 0
        self.latched = False

    def observe(self, detected: bool) -> bool:
        if self.latched:
            return False
        self.consecutive_frames = self.consecutive_frames + 1 if detected else 0
        if self.consecutive_frames >= self.required_frames:
            self.latched = True
            return True
        return False


class StopConfirmationTracker:
    """Keep service acceptance separate from evidence that the robot stopped."""

    def __init__(
        self,
        *,
        require_odometry: bool,
        stationary_samples: int,
        linear_threshold: float,
        angular_threshold: float,
    ) -> None:
        if stationary_samples < 1:
            raise ValueError('stationary_samples must be at least 1')
        if linear_threshold < 0.0 or angular_threshold < 0.0:
            raise ValueError('stationary thresholds must be non-negative')
        self.require_odometry = bool(require_odometry)
        self.stationary_samples = int(stationary_samples)
        self.linear_threshold = float(linear_threshold)
        self.angular_threshold = float(angular_threshold)
        self.request_dispatched = False
        self.service_accepted = False
        self.stop_hold_observed = False
        self.stationary_count = 0

    def mark_request_dispatched(self) -> None:
        self.request_dispatched = True
        self.service_accepted = False
        self.stop_hold_observed = False
        self.stationary_count = 0

    def mark_service_accepted(self) -> None:
        # Evidence received before the successful response is not evidence of
        # this request.  Reset defensively even though the ROS subscriptions
        # are also created only after acceptance.
        self.service_accepted = True
        self.stop_hold_observed = False
        self.stationary_count = 0

    def observe_stop_hold(self, active: bool) -> None:
        if self.service_accepted and active:
            self.stop_hold_observed = True

    def observe_odometry(self, linear_speed: float, angular_speed: float) -> None:
        if not self.service_accepted:
            return
        stationary = (
            abs(float(linear_speed)) <= self.linear_threshold
            and abs(float(angular_speed)) <= self.angular_threshold
        )
        self.stationary_count = self.stationary_count + 1 if stationary else 0

    @property
    def confirmed(self) -> bool:
        odometry_ok = (
            not self.require_odometry
            or self.stationary_count >= self.stationary_samples
        )
        return self.service_accepted and self.stop_hold_observed and odometry_ok


class YoloPersonDetector:
    """Minimal Camera V7-derived YOLO person-class detector."""

    def __init__(self, model_path: str, confidence: float, image_size: int) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                'ultralytics is required for YOLO inference; install requirements.txt '
                'in the Python environment used to run this node'
            ) from exc
        self._model = YOLO(model_path)
        self._confidence = float(confidence)
        self._image_size = int(image_size)

    def detect(self, frame: Any) -> DetectionObservation:
        results = self._model.predict(
            frame,
            classes=[0],
            conf=self._confidence,
            imgsz=self._image_size,
            verbose=False,
        )
        if not results or results[0].boxes is None or len(results[0].boxes) == 0:
            return DetectionObservation(False)
        boxes = results[0].boxes
        confidences = [float(value) for value in boxes.conf.detach().cpu().tolist()]
        person_confidences = [
            confidence
            for class_id, confidence in zip(
                boxes.cls.detach().cpu().tolist(), confidences
            )
            if int(class_id) == 0 and confidence >= self._confidence
        ]
        if not person_confidences:
            return DetectionObservation(False)
        return DetectionObservation(
            True,
            max_confidence=max(person_confidences),
            count=len(person_confidences),
        )


class CameraOwnershipError(RuntimeError):
    """Raised before opening a camera when exclusive ownership is not safe."""


class AdvisoryCameraLock:
    """Keep one cooperating process as the direct owner of a camera device."""

    def __init__(self, source: str, lock_path: str = '') -> None:
        digest = hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]
        self.path = Path(lock_path or f'/tmp/care_patrol_camera_{digest}.lock')
        self.source = source
        self._handle: Optional[Any] = None

    def acquire(self) -> None:
        self._handle = self.path.open('a+', encoding='utf-8')
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise CameraOwnershipError(
                f'camera ownership lock is already held: {self.path}'
            ) from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(f'pid={os.getpid()} source={self.source}\n')
        self._handle.flush()

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def camera_v7_processes() -> Sequence[str]:
    """Best-effort detection of Camera V7 processes that may own the C920."""
    matches = []
    own_pid = os.getpid()
    patterns = (
        'daikoudai_v7.py',
        'person_search_vlm_milestone2_v7.py',
        'camera_all_in_one.py',
    )
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == own_pid:
            continue
        try:
            command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(
                'utf-8', errors='replace'
            )
        except (OSError, PermissionError):
            continue
        if any(pattern in command for pattern in patterns):
            matches.append(command.strip())
    return matches


class OpenCvFrameSource:
    """One OpenCV capture owner for either a saved video or direct camera."""

    def __init__(
        self,
        *,
        input_mode: str,
        video_path: str,
        camera_device: str,
        allow_direct_camera: bool,
        refuse_if_camera_v7_running: bool,
        camera_lock_path: str,
        camera_width: int,
        camera_height: int,
    ) -> None:
        self.input_mode = input_mode
        self.camera_device = camera_device
        self._lock: Optional[AdvisoryCameraLock] = None
        self._capture: Optional[cv2.VideoCapture] = None

        if input_mode == 'video':
            if not video_path:
                raise ValueError('video_path is required when input_mode=video')
            path = Path(video_path).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f'video input does not exist: {path}')
            source: Any = str(path)
            api = cv2.CAP_ANY
        elif input_mode == 'camera':
            if not allow_direct_camera:
                raise CameraOwnershipError(
                    'direct camera open is disabled; set allow_direct_camera=true only '
                    'after confirming Camera V7 is stopped'
                )
            running = camera_v7_processes() if refuse_if_camera_v7_running else []
            if running:
                raise CameraOwnershipError(
                    'Camera V7 process detected; refusing a second C920 open: '
                    + running[0]
                )
            if not camera_device:
                raise ValueError('camera_device is required when input_mode=camera')
            self._lock = AdvisoryCameraLock(camera_device, camera_lock_path)
            self._lock.acquire()
            source = int(camera_device) if camera_device.isdigit() else camera_device
            api = cv2.CAP_V4L2
        else:
            raise ValueError('input_mode must be "video" or "camera"')

        self._capture = cv2.VideoCapture(source, api)
        if input_mode == 'camera':
            if camera_width > 0:
                self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, camera_width)
            if camera_height > 0:
                self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_height)
        if not self._capture.isOpened():
            self.close()
            raise RuntimeError(f'failed to open {input_mode} input: {source}')

    def read(self) -> tuple[bool, Any]:
        if self._capture is None:
            return False, None
        return self._capture.read()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        if self._lock is not None:
            self._lock.release()
            self._lock = None


class PersonStopGuard(Node):
    """ROS 2 node that latches one person-triggered pause request."""

    def __init__(
        self,
        *,
        detector: Optional[Any] = None,
        frame_source: Optional[Any] = None,
        parameter_overrides: Optional[Sequence[Parameter]] = None,
    ) -> None:
        super().__init__(
            'person_stop_guard',
            parameter_overrides=list(parameter_overrides or []),
        )
        self._declare_parameters()
        self._load_parameters()
        self.state = StopState.MONITORING
        self._request_future: Optional[Any] = None
        self._request_sent_at: Optional[float] = None
        self._person_confirmed_at: Optional[float] = None
        self._confirmation_timeout_warned = False
        self._frame_source = frame_source
        self._processing_timer = None

        self._gate = ConsecutivePersonGate(self.required_consecutive_frames)
        self._confirmation = StopConfirmationTracker(
            require_odometry=self.require_odometry_confirmation,
            stationary_samples=self.stationary_samples,
            linear_threshold=self.stationary_linear_threshold,
            angular_threshold=self.stationary_angular_threshold,
        )

        self._pause_client = self.create_client(SetBool, self.pause_service)
        self._stop_subscription = None
        self._odometry_subscription = None

        try:
            self._detector = detector or YoloPersonDetector(
                self.yolo_model, self.confidence, self.yolo_image_size
            )
            self._frame_source = self._frame_source or OpenCvFrameSource(
                input_mode=self.input_mode,
                video_path=self.video_path,
                camera_device=self.camera_device,
                allow_direct_camera=self.allow_direct_camera,
                refuse_if_camera_v7_running=self.refuse_if_camera_v7_running,
                camera_lock_path=self.camera_lock_path,
                camera_width=self.camera_width,
                camera_height=self.camera_height,
            )
        except Exception as exc:
            self.state = StopState.FAULT
            self.get_logger().error(f'initialization failed: {type(exc).__name__}: {exc}')
            raise

        self._processing_timer = self.create_timer(
            1.0 / self.processing_rate_hz, self._process_frame
        )
        self._status_timer = self.create_timer(0.1, self._check_timeouts)
        self.get_logger().info(
            f'monitoring {self.input_mode} input; confidence={self.confidence:.3f}, '
            f'consecutive_frames={self.required_consecutive_frames}'
        )

    def _declare_parameters(self) -> None:
        defaults = (
            ('input_mode', 'video'),
            ('video_path', ''),
            ('camera_device', '/dev/video0'),
            ('allow_direct_camera', False),
            ('refuse_if_camera_v7_running', True),
            ('camera_lock_path', '/tmp/care_patrol_c920.lock'),
            ('camera_width', 640),
            ('camera_height', 480),
            ('yolo_model', 'yolo11n.pt'),
            ('confidence', 0.5),
            ('yolo_image_size', 640),
            ('required_consecutive_frames', 3),
            ('processing_rate_hz', 5.0),
            ('pause_service', '/koko_waypoint_follower/set_paused'),
            ('service_wait_timeout_sec', 5.0),
            ('service_response_timeout_sec', 5.0),
            ('stop_confirmation_timeout_sec', 10.0),
            ('stop_confirmation_topic', '/stop'),
            ('odometry_topic', '/odom'),
            ('require_odometry_confirmation', True),
            ('stationary_samples', 3),
            ('stationary_linear_threshold', 0.03),
            ('stationary_angular_threshold', 0.05),
        )
        for name, default in defaults:
            self.declare_parameter(name, default)

    def _load_parameters(self) -> None:
        for name in (
            'input_mode', 'video_path', 'camera_device', 'camera_lock_path',
            'yolo_model', 'pause_service', 'stop_confirmation_topic',
            'odometry_topic',
        ):
            setattr(self, name, str(self.get_parameter(name).value))
        for name in (
            'allow_direct_camera', 'refuse_if_camera_v7_running',
            'require_odometry_confirmation',
        ):
            setattr(self, name, bool(self.get_parameter(name).value))
        for name in (
            'camera_width', 'camera_height', 'yolo_image_size',
            'required_consecutive_frames', 'stationary_samples',
        ):
            setattr(self, name, int(self.get_parameter(name).value))
        for name in (
            'confidence', 'processing_rate_hz', 'service_wait_timeout_sec',
            'service_response_timeout_sec', 'stop_confirmation_timeout_sec',
            'stationary_linear_threshold', 'stationary_angular_threshold',
        ):
            setattr(self, name, float(self.get_parameter(name).value))
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError('confidence must be in (0.0, 1.0]')
        if self.processing_rate_hz <= 0.0:
            raise ValueError('processing_rate_hz must be positive')
        if self.service_wait_timeout_sec <= 0.0:
            raise ValueError('service_wait_timeout_sec must be positive')
        if self.service_response_timeout_sec <= 0.0:
            raise ValueError('service_response_timeout_sec must be positive')
        if self.stop_confirmation_timeout_sec <= 0.0:
            raise ValueError('stop_confirmation_timeout_sec must be positive')

    def _process_frame(self) -> None:
        if self.state is not StopState.MONITORING:
            return
        ok, frame = self._frame_source.read()
        if not ok or frame is None:
            self._stop_input()
            self._set_state(
                StopState.INPUT_ENDED_NO_DETECTION,
                'input ended before the configured person condition was met',
            )
            return
        try:
            observation = self._detector.detect(frame)
        except Exception as exc:
            self._stop_input()
            self._set_state(
                StopState.FAULT,
                f'YOLO inference failed: {type(exc).__name__}: {exc}',
                error=True,
            )
            return
        if self._gate.observe(observation.detected):
            self._person_confirmed_at = monotonic()
            self.get_logger().warning(
                f'person condition met: confidence={observation.max_confidence:.3f}, '
                f'count={observation.count}; requesting pause once'
            )
            self._stop_input()
            self._set_state(StopState.WAITING_FOR_SERVICE, 'person stop latched')
            self._try_dispatch_pause()

    def _try_dispatch_pause(self) -> None:
        if self._request_future is not None or self._confirmation.request_dispatched:
            return
        if not self._pause_client.service_is_ready():
            return
        request = SetBool.Request()
        request.data = True
        self._confirmation.mark_request_dispatched()
        self._request_sent_at = monotonic()
        self._request_future = self._pause_client.call_async(request)
        self._request_future.add_done_callback(self._on_pause_response)
        self._set_state(StopState.REQUEST_SENT, 'pause request dispatched with data=True')

    def _on_pause_response(self, future: Any) -> None:
        if self.state is not StopState.REQUEST_SENT:
            return
        try:
            response = future.result()
        except Exception as exc:
            self._set_state(
                StopState.REQUEST_OUTCOME_UNKNOWN,
                f'pause response failed; request will not be resent: {exc}',
                error=True,
            )
            return
        if response is None or not response.success:
            message = '' if response is None else response.message
            self._set_state(
                StopState.REQUEST_REJECTED,
                f'pause request rejected: {message}',
                error=True,
            )
            return
        self._confirmation.mark_service_accepted()
        self._start_confirmation_observers()
        self._set_state(
            StopState.REQUEST_ACCEPTED_UNCONFIRMED,
            f'pause accepted but physical stop is not yet confirmed: {response.message}',
        )
        self._update_confirmation_state()

    def _start_confirmation_observers(self) -> None:
        """Subscribe after acceptance so volatile QoS excludes old samples."""
        if self._stop_subscription is None:
            stop_qos = QoSProfile(
                depth=10,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.VOLATILE,
            )
            self._stop_subscription = self.create_subscription(
                Bool,
                self.stop_confirmation_topic,
                self._on_stop_hold,
                stop_qos,
            )
        if self.require_odometry_confirmation and self._odometry_subscription is None:
            self._odometry_subscription = self.create_subscription(
                Odometry,
                self.odometry_topic,
                self._on_odometry,
                qos_profile_sensor_data,
            )

    def _on_stop_hold(self, message: Bool) -> None:
        self._confirmation.observe_stop_hold(bool(message.data))
        self._update_confirmation_state()

    def _on_odometry(self, message: Odometry) -> None:
        linear = message.twist.twist.linear
        angular = message.twist.twist.angular
        linear_speed = math.sqrt(linear.x ** 2 + linear.y ** 2 + linear.z ** 2)
        angular_speed = math.sqrt(angular.x ** 2 + angular.y ** 2 + angular.z ** 2)
        self._confirmation.observe_odometry(linear_speed, angular_speed)
        self._update_confirmation_state()

    def _update_confirmation_state(self) -> None:
        if (
            self.state is StopState.REQUEST_ACCEPTED_UNCONFIRMED
            and self._confirmation.confirmed
        ):
            self._set_state(
                StopState.STOP_CONFIRMED,
                'stop hold and stationary odometry observed; automatic resume is disabled',
            )

    def _check_timeouts(self) -> None:
        now = monotonic()
        if self.state is StopState.WAITING_FOR_SERVICE:
            self._try_dispatch_pause()
            if (
                self._person_confirmed_at is not None
                and now - self._person_confirmed_at >= self.service_wait_timeout_sec
            ):
                self._set_state(
                    StopState.SERVICE_UNAVAILABLE,
                    'pause service did not become available; no request was sent',
                    error=True,
                )
        elif (
            self.state is StopState.REQUEST_SENT
            and self._request_sent_at is not None
            and now - self._request_sent_at >= self.service_response_timeout_sec
        ):
            self._set_state(
                StopState.REQUEST_OUTCOME_UNKNOWN,
                'pause response timed out; request will not be resent',
                error=True,
            )
        elif (
            self.state is StopState.REQUEST_ACCEPTED_UNCONFIRMED
            and self._request_sent_at is not None
            and not self._confirmation_timeout_warned
            and now - self._request_sent_at >= self.stop_confirmation_timeout_sec
        ):
            self._confirmation_timeout_warned = True
            missing = []
            if not self._confirmation.stop_hold_observed:
                missing.append(self.stop_confirmation_topic)
            if (
                self.require_odometry_confirmation
                and self._confirmation.stationary_count < self.stationary_samples
            ):
                missing.append(f'stationary {self.odometry_topic}')
            self.get_logger().error(
                'pause was accepted, but stop remains UNCONFIRMED; missing: '
                + ', '.join(missing)
            )
        if (
            self.input_mode == 'camera'
            and self.state is StopState.MONITORING
            and self.refuse_if_camera_v7_running
            and camera_v7_processes()
        ):
            self._stop_input()
            self._set_state(
                StopState.FAULT,
                'Camera V7 started while direct C920 ownership was active; camera released',
                error=True,
            )

    def _set_state(self, state: StopState, message: str, *, error: bool = False) -> None:
        if self.state is StopState.STOP_CONFIRMED:
            return
        self.state = state
        text = f'state={state.value}: {message}'
        if error:
            self.get_logger().error(text)
        elif state in {
            StopState.REQUEST_ACCEPTED_UNCONFIRMED,
            StopState.STOP_CONFIRMED,
        }:
            self.get_logger().warning(text)
        else:
            self.get_logger().info(text)

    def _stop_input(self) -> None:
        if self._processing_timer is not None:
            self._processing_timer.cancel()
        if self._frame_source is not None:
            self._frame_source.close()

    def destroy_node(self) -> bool:
        self._stop_input()
        return super().destroy_node()


def main(args: Optional[Sequence[str]] = None) -> int:
    rclpy.init(args=args)
    node: Optional[PersonStopGuard] = None
    executor: Optional[MultiThreadedExecutor] = None
    try:
        node = PersonStopGuard()
        executor = MultiThreadedExecutor(num_threads=2)
        executor.add_node(node)
        executor.spin()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        if node is not None:
            node.get_logger().fatal(f'person_stop_guard terminated: {exc}')
        else:
            print(f'person_stop_guard initialization failed: {exc}')
        return 1
    finally:
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    raise SystemExit(main())
