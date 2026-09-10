"""
Multi-camera manager
Supports USB, RTSP, video files, and Intel RealSense depth cameras.
"""
import cv2
import numpy as np
import os
import threading
import time
from typing import Dict, Tuple, Optional, List, Any
from queue import Queue, Empty
import logging


logger = logging.getLogger(__name__)

# pyrealsense2 is only needed if a camera source uses "realsense://" scheme.
# Import lazily so systems without the SDK (or without Intel cameras) still run.
_REALSENSE_AVAILABLE = False
_rs = None
try:
    import pyrealsense2 as _rs  # type: ignore
    _REALSENSE_AVAILABLE = True
except ImportError:
    pass


def _is_realsense_source(source: Any) -> bool:
    """True if the source string targets an Intel RealSense device."""
    return isinstance(source, str) and source.startswith("realsense://")


def _parse_realsense_serial(source: str) -> Optional[str]:
    """Extract optional serial from 'realsense://<serial>'. Empty -> first device."""
    suffix = source[len("realsense://"):].strip()
    return suffix or None


class CameraSource:
    """Single camera source"""

    # Reconnect policy for network sources (RTSP / HTTP).
    _FAIL_THRESHOLD_BEFORE_RECONNECT = 30  # consecutive failed reads
    _RECONNECT_BACKOFF_MIN_SEC = 2.0
    _RECONNECT_BACKOFF_MAX_SEC = 30.0

    def __init__(self, camera_id: str, source: Any, resolution: Tuple[int, int] = None):
        """
        Args:
            camera_id: Unique camera identifier
            source: Input source. Supported forms:
                - int (USB device number)
                - "/dev/videoN" or path string
                - "rtsp://..." / "http://..." (network stream)
                - "<file>.mp4" / "<file>.mov" (video file)
                - "realsense://" or "realsense://<serial>" (Intel RealSense)
            resolution: Target color resolution (width, height)
        """
        self.camera_id = camera_id
        self.source = source
        self.resolution = resolution or (1280, 720)

        # "cv2" (default) | "realsense"
        self._mode: str = "realsense" if _is_realsense_source(source) else "cv2"

        self.cap = None
        self.is_opened = False

        # RealSense pipeline state (mode == "realsense" only)
        self._rs_pipeline = None
        self._rs_align = None
        self._rs_depth_scale: float = 0.001  # meters per uint16 unit; refreshed on open
        self.depth_intrinsics = None         # rs.intrinsics, kept for future 3D unprojection

        self._frame_queue = Queue(maxsize=2)  # Keep only the latest 1-2 frames
        self._thread = None
        self._stop_flag = False

        self.frame_count = 0
        self.fps = 0.0
        self.last_fps_time = time.time()

        # Reliability counters (used by /health)
        self.reconnect_count = 0
        self.last_reconnect_time: float = 0.0
        self.last_successful_read_time: float = time.time()
        self.video_loop_count = 0

        # Ring buffer: recent frames with timestamps, used by SnapshotManager
        from collections import deque
        self.frame_ring_buffer: deque = deque(maxlen=120)  # ~4s at 30fps

        # Latest aligned depth frame (uint16, mm). None when no depth source.
        self._depth_lock = threading.Lock()
        self.latest_depth: Optional[np.ndarray] = None
        self.latest_depth_ts: float = 0.0

    def open(self) -> bool:
        """Open camera (dispatches by source type)."""
        if self._mode == "realsense":
            return self._open_realsense()
        return self._open_cv2()

    def _open_cv2(self) -> bool:
        """Open a cv2.VideoCapture-backed source (USB, RTSP, HTTP, file)."""
        try:
            if isinstance(self.source, int):
                # On macOS, force AVFoundation backend so the index mapping
                # matches what our tools/ scripts (uvc_preview, auto_snapshot,
                # probe_devices) see. Without this, cv2 may pick a different
                # default backend whose enumeration order differs — leading
                # to "source=1 opened MacBook camera instead of RealSense".
                import sys as _sys
                if _sys.platform == "darwin":
                    self.cap = cv2.VideoCapture(self.source, cv2.CAP_AVFOUNDATION)
                else:
                    self.cap = cv2.VideoCapture(self.source)
            else:
                self.cap = cv2.VideoCapture(str(self.source))

            if not self.cap.isOpened():
                logger.error(f"Failed to open camera {self.camera_id}: {self.source}")
                return False

            # Set resolution
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])

            self.is_opened = True

            # For video files, read native FPS for throttling
            src = str(self.source)
            self._video_frame_interval: float = 0.0
            if not isinstance(self.source, int) and not src.startswith(('http', 'rtsp')):
                native_fps = self.cap.get(cv2.CAP_PROP_FPS)
                if native_fps > 0:
                    self._video_frame_interval = 1.0 / native_fps
                    logger.info(f"Camera {self.camera_id} video FPS={native_fps:.1f}, throttling to match")

            logger.info(f"Camera {self.camera_id} opened: {self.source} @ {self.resolution}")

            # Start reading thread
            self._stop_flag = False
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()

            return True

        except Exception as e:
            logger.error(f"Error opening camera {self.camera_id}: {e}")
            return False

    def _open_realsense(self) -> bool:
        """Open an Intel RealSense device. Configures color + depth, aligned.

        Reads tunables from src.utils.config.REALSENSE_CONFIG so depth range,
        FPS, and emitter behavior are not hard-coded here.
        """
        if not _REALSENSE_AVAILABLE:
            logger.error(
                f"Camera {self.camera_id}: source is {self.source!r} but pyrealsense2 "
                "is not installed. Add 'pip install pyrealsense2' to enable RealSense support."
            )
            return False

        # Local import keeps the file usable even when config can't be loaded
        try:
            from ..utils.config import REALSENSE_CONFIG
        except Exception:
            REALSENSE_CONFIG = {}

        serial = _parse_realsense_serial(self.source)
        color_w, color_h = self.resolution
        depth_w = REALSENSE_CONFIG.get("depth_width", 848)
        depth_h = REALSENSE_CONFIG.get("depth_height", 480)
        depth_fps = REALSENSE_CONFIG.get("depth_fps", 30)
        color_fps = REALSENSE_CONFIG.get("color_fps", 30)

        try:
            ctx = _rs.context()
            devices = ctx.query_devices()
            if len(devices) == 0:
                logger.error(f"Camera {self.camera_id}: no RealSense devices found on USB")
                return False

            cfg = _rs.config()
            if serial:
                cfg.enable_device(serial)
            cfg.enable_stream(_rs.stream.color, color_w, color_h, _rs.format.bgr8, color_fps)
            cfg.enable_stream(_rs.stream.depth, depth_w, depth_h, _rs.format.z16, depth_fps)

            self._rs_pipeline = _rs.pipeline()
            profile = self._rs_pipeline.start(cfg)

            # Depth scale tells us how to convert raw uint16 -> meters.
            # D455 is typically 0.001 (1 mm per unit) but we read it explicitly
            # to be robust across firmwares / variants.
            depth_sensor = profile.get_device().first_depth_sensor()
            self._rs_depth_scale = float(depth_sensor.get_depth_scale())

            # Toggle IR emitter — on for indoor, off would be set explicitly if needed.
            try:
                if depth_sensor.supports(_rs.option.emitter_enabled):
                    depth_sensor.set_option(
                        _rs.option.emitter_enabled,
                        1 if REALSENSE_CONFIG.get("emitter_enabled", True) else 0,
                    )
            except Exception as e:
                logger.debug(f"Camera {self.camera_id}: could not set emitter option: {e}")

            # Align depth to color so a (col_x, col_y) maps directly into the depth array.
            if REALSENSE_CONFIG.get("align_to_color", True):
                self._rs_align = _rs.align(_rs.stream.color)

            # Cache color intrinsics — useful later for 3D unprojection.
            try:
                color_stream = profile.get_stream(_rs.stream.color)
                self.depth_intrinsics = color_stream.as_video_stream_profile().get_intrinsics()
            except Exception:
                self.depth_intrinsics = None

            self.is_opened = True
            self._video_frame_interval = 0.0  # live device, no throttling

            logger.info(
                f"Camera {self.camera_id} opened (RealSense): serial={serial or 'auto'} "
                f"color={color_w}x{color_h}@{color_fps} depth={depth_w}x{depth_h}@{depth_fps} "
                f"scale={self._rs_depth_scale:.5f} m/unit"
            )

            self._stop_flag = False
            self._thread = threading.Thread(target=self._read_loop_realsense, daemon=True)
            self._thread.start()
            return True

        except Exception as e:
            logger.error(f"Camera {self.camera_id}: RealSense open failed: {e}")
            try:
                if self._rs_pipeline is not None:
                    self._rs_pipeline.stop()
            except Exception:
                pass
            self._rs_pipeline = None
            return False

    def _is_network_source(self) -> bool:
        """True for RTSP / HTTP / USB / RealSense sources where reconnect makes sense."""
        if isinstance(self.source, int):
            return True  # USB device — also worth reconnecting after unplug
        if self._mode == "realsense":
            return True  # USB-attached RealSense should self-heal on cable bumps
        src = str(self.source)
        return src.startswith(("rtsp", "http"))

    def _is_video_file_source(self) -> bool:
        return (
            isinstance(self.source, str)
            and not self._is_network_source()
            and os.path.isfile(self.source)
        )

    def _reopen_capture(self) -> bool:
        """Tear down and rebuild cv2.VideoCapture. Returns True on success."""
        try:
            if self.cap is not None:
                try:
                    self.cap.release()
                except Exception:
                    pass
                self.cap = None

            if isinstance(self.source, int):
                cap = cv2.VideoCapture(self.source)
            else:
                cap = cv2.VideoCapture(str(self.source))

            if not cap.isOpened():
                return False

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.resolution[0])
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[1])
            self.cap = cap
            return True
        except Exception as e:
            logger.error(f"Camera {self.camera_id} reopen failed: {e}")
            return False

    def _reconnect_with_backoff(self) -> bool:
        """Attempt to rebuild the capture with exponential backoff.

        Returns False only if a clean shutdown was requested (_stop_flag).
        Otherwise loops until reconnection succeeds — network sources should
        never give up, so the main loop is oblivious to transient drops.
        """
        backoff = self._RECONNECT_BACKOFF_MIN_SEC
        attempt = 0
        while not self._stop_flag:
            attempt += 1
            logger.warning(
                f"Camera {self.camera_id} reconnect attempt {attempt} "
                f"(backoff={backoff:.1f}s)"
            )
            if self._reopen_capture():
                self.reconnect_count += 1
                self.last_reconnect_time = time.time()
                self.last_successful_read_time = time.time()
                logger.info(
                    f"Camera {self.camera_id} reconnected after {attempt} attempt(s) "
                    f"(total reconnects: {self.reconnect_count})"
                )
                return True
            # Sleep in small chunks so stop_flag is honored promptly
            slept = 0.0
            while slept < backoff and not self._stop_flag:
                time.sleep(min(0.5, backoff - slept))
                slept += 0.5
            backoff = min(backoff * 2, self._RECONNECT_BACKOFF_MAX_SEC)
        return False

    def _read_loop_realsense(self):
        """Background thread for RealSense devices.

        Pulls frames from the pipeline, aligns depth to color (if configured),
        and publishes color into the standard frame queue while stashing the
        raw uint16 depth array under self.latest_depth for later sampling.
        Reconnect logic mirrors the cv2 loop: persistent failure tears down
        and rebuilds the pipeline with exponential backoff.
        """
        fail_count = 0
        while not self._stop_flag:
            try:
                # 1s timeout — long enough to ride through transient hiccups,
                # short enough to honor stop_flag promptly on shutdown.
                frames = self._rs_pipeline.wait_for_frames(timeout_ms=1000)

                if self._rs_align is not None:
                    frames = self._rs_align.process(frames)

                color_frame = frames.get_color_frame()
                depth_frame = frames.get_depth_frame()

                if not color_frame or not depth_frame:
                    fail_count += 1
                    if fail_count >= self._FAIL_THRESHOLD_BEFORE_RECONNECT:
                        logger.error(
                            f"Camera {self.camera_id} RealSense empty frames {fail_count}× "
                            "— attempting reconnect"
                        )
                        if not self._reconnect_realsense_with_backoff():
                            break
                        fail_count = 0
                    continue

                fail_count = 0
                self.last_successful_read_time = time.time()

                color_np = np.asanyarray(color_frame.get_data())
                depth_np = np.asanyarray(depth_frame.get_data())  # uint16, mm-ish

                with self._depth_lock:
                    self.latest_depth = depth_np
                    self.latest_depth_ts = time.time()

                # Clear queue, keep only latest color frame
                try:
                    while True:
                        self._frame_queue.get_nowait()
                except Empty:
                    pass

                self._frame_queue.put((True, color_np))
                self.frame_ring_buffer.append((time.time(), color_np))
                self.frame_count += 1

                now = time.time()
                if now - self.last_fps_time > 1.0:
                    self.fps = self.frame_count / (now - self.last_fps_time)
                    self.frame_count = 0
                    self.last_fps_time = now

            except Exception as e:
                logger.error(f"Camera {self.camera_id} RealSense read error: {e}")
                if not self._reconnect_realsense_with_backoff():
                    break
                fail_count = 0

    def _reconnect_realsense_with_backoff(self) -> bool:
        """Tear down and rebuild the RealSense pipeline with exponential backoff.

        Returns False only if a clean shutdown was requested.
        """
        backoff = self._RECONNECT_BACKOFF_MIN_SEC
        attempt = 0
        try:
            if self._rs_pipeline is not None:
                self._rs_pipeline.stop()
        except Exception:
            pass
        self._rs_pipeline = None
        self._rs_align = None

        while not self._stop_flag:
            attempt += 1
            logger.warning(
                f"Camera {self.camera_id} RealSense reconnect attempt {attempt} "
                f"(backoff={backoff:.1f}s)"
            )
            # Reuse the original open path so config tunables apply consistently.
            if self._open_realsense_pipeline_only():
                self.reconnect_count += 1
                self.last_reconnect_time = time.time()
                self.last_successful_read_time = time.time()
                logger.info(
                    f"Camera {self.camera_id} RealSense reconnected after {attempt} attempt(s) "
                    f"(total reconnects: {self.reconnect_count})"
                )
                return True
            slept = 0.0
            while slept < backoff and not self._stop_flag:
                time.sleep(min(0.5, backoff - slept))
                slept += 0.5
            backoff = min(backoff * 2, self._RECONNECT_BACKOFF_MAX_SEC)
        return False

    def _open_realsense_pipeline_only(self) -> bool:
        """Re-establish the pipeline without restarting the read thread.

        Used by _reconnect_realsense_with_backoff(). Returns True on success.
        """
        if not _REALSENSE_AVAILABLE:
            return False
        try:
            from ..utils.config import REALSENSE_CONFIG
        except Exception:
            REALSENSE_CONFIG = {}

        serial = _parse_realsense_serial(self.source)
        color_w, color_h = self.resolution
        depth_w = REALSENSE_CONFIG.get("depth_width", 848)
        depth_h = REALSENSE_CONFIG.get("depth_height", 480)
        depth_fps = REALSENSE_CONFIG.get("depth_fps", 30)
        color_fps = REALSENSE_CONFIG.get("color_fps", 30)

        try:
            cfg = _rs.config()
            if serial:
                cfg.enable_device(serial)
            cfg.enable_stream(_rs.stream.color, color_w, color_h, _rs.format.bgr8, color_fps)
            cfg.enable_stream(_rs.stream.depth, depth_w, depth_h, _rs.format.z16, depth_fps)
            self._rs_pipeline = _rs.pipeline()
            profile = self._rs_pipeline.start(cfg)
            depth_sensor = profile.get_device().first_depth_sensor()
            self._rs_depth_scale = float(depth_sensor.get_depth_scale())
            if REALSENSE_CONFIG.get("align_to_color", True):
                self._rs_align = _rs.align(_rs.stream.color)
            return True
        except Exception as e:
            logger.error(f"Camera {self.camera_id} RealSense pipeline rebuild failed: {e}")
            try:
                if self._rs_pipeline is not None:
                    self._rs_pipeline.stop()
            except Exception:
                pass
            self._rs_pipeline = None
            return False

    # ------------------------------------------------------------------
    # Depth sampling API (returns None on non-depth sources or invalid data)
    # ------------------------------------------------------------------

    def has_depth(self) -> bool:
        """True if this camera publishes a depth stream alongside color."""
        return self._mode == "realsense"

    def sample_depth_at(self, x: int, y: int) -> Optional[float]:
        """Return depth in meters at color pixel (x, y), or None if invalid."""
        if not self.has_depth():
            return None
        with self._depth_lock:
            depth = self.latest_depth
        if depth is None:
            return None
        h, w = depth.shape[:2]
        if x < 0 or y < 0 or x >= w or y >= h:
            return None
        raw = int(depth[y, x])
        if raw == 0:
            return None
        meters = raw * self._rs_depth_scale
        return self._validate_depth_m(meters)

    def sample_depth_in_bbox(self, bbox) -> Optional[float]:
        """Median depth (meters) inside the central region of an xyxy bbox.

        Sampling only the center fraction of the bbox biases toward the
        torso — bbox edges often catch background pixels which would skew
        the median toward "wall" instead of "person".
        """
        if not self.has_depth():
            return None
        with self._depth_lock:
            depth = self.latest_depth
        if depth is None:
            return None
        try:
            x1, y1, x2, y2 = (int(v) for v in bbox[:4])
        except (TypeError, ValueError):
            return None
        if x2 <= x1 or y2 <= y1:
            return None

        try:
            from ..utils.config import REALSENSE_CONFIG
            ratio = float(REALSENSE_CONFIG.get("bbox_depth_sample_ratio", 0.33))
        except Exception:
            ratio = 0.33
        ratio = max(0.05, min(1.0, ratio))

        bw = x2 - x1
        bh = y2 - y1
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        half_w = max(1, int(bw * ratio / 2))
        half_h = max(1, int(bh * ratio / 2))

        h, w = depth.shape[:2]
        sx1 = max(0, cx - half_w)
        sy1 = max(0, cy - half_h)
        sx2 = min(w, cx + half_w)
        sy2 = min(h, cy + half_h)
        if sx2 <= sx1 or sy2 <= sy1:
            return None

        roi = depth[sy1:sy2, sx1:sx2]
        valid = roi[roi > 0]
        if valid.size == 0:
            return None
        meters = float(np.median(valid)) * self._rs_depth_scale
        return self._validate_depth_m(meters)

    def _validate_depth_m(self, meters: float) -> Optional[float]:
        """Apply config-defined min/max bounds; return None if out of range."""
        try:
            from ..utils.config import REALSENSE_CONFIG
            dmin = float(REALSENSE_CONFIG.get("depth_min_m", 0.3))
            dmax = float(REALSENSE_CONFIG.get("depth_max_m", 6.0))
        except Exception:
            dmin, dmax = 0.3, 6.0
        if meters < dmin or meters > dmax:
            return None
        return meters

    def _read_loop(self):
        """Background thread: continuously read camera data with self-healing.

        File sources stop cleanly on EOF. Network / device sources reconnect
        indefinitely with exponential backoff when reads fail.
        """
        fail_count = 0
        while not self._stop_flag:
            try:
                ok, frame = self.cap.read()
                if not ok:
                    if (
                        self._is_video_file_source()
                        and os.getenv("LOOP_VIDEO_FILES", "false").lower() == "true"
                        and self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ):
                        self.video_loop_count += 1
                        logger.info(
                            f"Camera {self.camera_id} video loop "
                            f"{self.video_loop_count} started"
                        )
                        continue
                    if not self._is_network_source():
                        logger.info(f"Camera {self.camera_id} video ended, stopping")
                        self.is_opened = False
                        break
                    fail_count += 1
                    if fail_count >= self._FAIL_THRESHOLD_BEFORE_RECONNECT:
                        logger.error(
                            f"Camera {self.camera_id} read failed {fail_count}× — "
                            "attempting reconnect"
                        )
                        if not self._reconnect_with_backoff():
                            break  # stop requested
                        fail_count = 0
                        continue
                    time.sleep(0.1)
                    continue

                fail_count = 0
                self.last_successful_read_time = time.time()

                # Clear queue, keep only latest frame
                try:
                    while True:
                        self._frame_queue.get_nowait()
                except Empty:
                    pass

                self._frame_queue.put((ok, frame))
                self.frame_ring_buffer.append((time.time(), frame))
                self.frame_count += 1

                # Throttle to video native FPS
                if self._video_frame_interval > 0:
                    time.sleep(self._video_frame_interval)

                # Calculate FPS
                now = time.time()
                if now - self.last_fps_time > 1.0:
                    self.fps = self.frame_count / (now - self.last_fps_time)
                    self.frame_count = 0
                    self.last_fps_time = now

            except Exception as e:
                logger.error(f"Camera {self.camera_id} read error: {e}")
                # Fatal read exception on a network source → trigger reconnect
                if self._is_network_source():
                    if not self._reconnect_with_backoff():
                        break
                    fail_count = 0
                    continue
                time.sleep(0.5)

    def read(self, timeout: float = 1.0) -> Tuple[bool, Any]:
        """
        Read latest frame

        Args:
            timeout: Timeout in seconds

        Returns:
            (success, frame)
        """
        try:
            ok, frame = self._frame_queue.get(timeout=timeout)
            return ok, frame
        except Empty:
            return False, None

    def release(self):
        """Release camera"""
        self._stop_flag = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
        if self._rs_pipeline is not None:
            try:
                self._rs_pipeline.stop()
            except Exception:
                pass
            self._rs_pipeline = None
            self._rs_align = None
        self.is_opened = False
        logger.info(f"Camera {self.camera_id} released")


class CameraManager:
    """Multi-camera manager"""

    def __init__(self):
        """Initialize camera manager"""
        self.cameras: Dict[str, CameraSource] = {}
        self.lock = threading.RLock()

    def register_camera(
        self,
        camera_id: str,
        source: Any,
        resolution: Tuple[int, int] = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """
        Register a camera

        Args:
            camera_id: Camera ID
            source: Input source
            resolution: Resolution

        Returns:
            Whether successful
        """
        with self.lock:
            if camera_id in self.cameras:
                logger.warning(f"Camera {camera_id} already registered")
                return False

        cam = CameraSource(camera_id, source, resolution)
        if not cam.open():
            cam.release()
            return False

        with self.lock:
            if cancel_event is not None and cancel_event.is_set():
                cam.release()
                return False
            if camera_id not in self.cameras:
                self.cameras[camera_id] = cam
                return True

        cam.release()
        logger.warning(f"Camera {camera_id} was registered concurrently")
        return False

    def read_frame(self, camera_id: str, timeout: float = 1.0) -> Tuple[bool, Any]:
        """Read a frame from a specific camera"""
        with self.lock:
            if camera_id not in self.cameras:
                return False, None
            return self.cameras[camera_id].read(timeout)

    def read_all_frames(self, timeout: float = 1.0) -> Dict[str, Tuple[bool, Any]]:
        """Read frames from all cameras"""
        result = {}
        with self.lock:
            for camera_id, cam in self.cameras.items():
                ok, frame = cam.read(timeout)
                result[camera_id] = (ok, frame)
        return result

    def get_camera_list(self) -> List[str]:
        """Get all camera IDs"""
        with self.lock:
            return list(self.cameras.keys())

    def get_camera(self, camera_id: str) -> Optional[CameraSource]:
        """Get camera object"""
        with self.lock:
            return self.cameras.get(camera_id)

    def get_fps(self, camera_id: str) -> float:
        """Get FPS for a specific camera"""
        with self.lock:
            if camera_id in self.cameras:
                return self.cameras[camera_id].fps
            return 0.0

    def release_camera(self, camera_id: str):
        """Release a specific camera"""
        with self.lock:
            if camera_id in self.cameras:
                self.cameras[camera_id].release()
                del self.cameras[camera_id]

    def release_all(self):
        """Release all cameras"""
        with self.lock:
            for cam in self.cameras.values():
                cam.release()
            self.cameras.clear()
        logger.info("All cameras released")
