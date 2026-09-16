# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Room Monitor — Main Coordinator (v2)

Architecture:
  - Fixed frame location (camera always at same angle)
  - Entry zone: new person tracks must appear here to count as "entered"
  - Mic zone: system only engages when person is in mic zone (or raises hand)
  - Per-person cache with appearance, invite state, 30s TTL
  - Debounced batch invite (no group tracking)

Threading model:
  Thread 1 (per camera): CameraSource._read_loop  — continuous frame capture
  Main thread:           YOLO detection + ReID + hand detection + orchestrator
  Worker:                VLMObserverWorker         — async VLM observations (daemon)
  Workers:               EventPublisher            — async HTTP delivery (daemon)
"""
import time
import threading
import cv2
import logging
import os
import numpy as np
from collections import defaultdict, deque
from typing import Dict, Optional

from ultralytics import YOLO

from ..utils.config import (
    CAMERAS, YOLO_CONFIG, HAND_RAISE_CONFIG, OVERHEAD_HAND_RAISE_CONFIG,
    PERSON_CONFIG, MULTI_CAM_CONFIG, EVENT_MANAGER_CONFIG, VISUALIZATION,
    AGENT_SERVER_CONFIG, TRACKING_CONFIG, VLM_CONFIG,
)
from ..utils.image import encode_crop
from .camera_manager import CameraManager
from .hand_detector import HandDetector
from .overhead_hand_detector import OverheadHandDetector
from .event_manager import EventManager, Event, EventType
from .person_reid import PersonReID
from .zone_detector import ZoneDetector
from .person_cache import PersonCache
from .scene_orchestrator import SceneOrchestrator
from .snapshot_manager import SnapshotManager
from ..services.vlm_observer import VLMObserverWorker
from ..services.event_publisher import EventPublisher

logger = logging.getLogger(__name__)

_ORCHESTRATOR_INTERVAL = TRACKING_CONFIG.get("orchestrator_interval", 0.2)


class RoomMonitor:
    """Room Monitoring System — Main Coordinator (v2)."""

    def __init__(self):
        # ---------- Infrastructure ----------
        self.camera_manager = CameraManager()
        self.model = YOLO(YOLO_CONFIG["model_path"])
        logger.info(f"YOLO model loaded: {YOLO_CONFIG['model_path']}")

        # ---------- Per-camera components ----------
        self.hand_detectors: Dict[str, HandDetector] = {}
        self._cam_tracker_states: Dict[str, list] = {}

        # ---------- Person tracking ----------
        self.event_manager = EventManager(
            max_history=EVENT_MANAGER_CONFIG["max_history_events"],
            history_window=EVENT_MANAGER_CONFIG["history_window_sec"],
            max_trajectory_length=PERSON_CONFIG.get("max_trajectory_length", 500),
        )
        self.reid = PersonReID(
            model_name="osnet_x0_25",
            match_threshold=MULTI_CAM_CONFIG.get("reid_threshold", 0.6),
        )
        self._reid_frame_count = defaultdict(int)
        self.MAX_PENDING_FRAMES = TRACKING_CONFIG.get("max_pending_frames", 30)
        self.last_raise_trigger = defaultdict(lambda: 0.0)
        self.person_frame_count = defaultdict(int)
        self._known_person_ids: set = set()

        # Centroid EMA smoothing: {global_person_id: (ema_x, ema_y)}
        self._centroid_ema: Dict[int, tuple] = {}
        self._centroid_ema_alpha: float = 0.4  # Higher = more responsive, lower = smoother

        # ---------- Zone detection + person cache + orchestration ----------
        self.zone_detector = ZoneDetector()
        self.person_cache = PersonCache()
        self.snapshot_manager = SnapshotManager()
        self._annotated_ring_buffers: Dict[str, deque] = {}

        self.event_publisher = EventPublisher()
        self.event_manager.register_callback(
            name="event_publisher_forward",
            callback=self.event_publisher.forward,
            events=None,
        )

        self.vlm_worker = VLMObserverWorker(
            result_cb=lambda group_id, result: None,  # Group-level VLM unused in v2
            appearance_cb=self._on_person_appearance,
        )

        self.orchestrator = SceneOrchestrator(
            publish_cb=self._on_orchestrator_event,
            person_cache=self.person_cache,
            zone_detector=self.zone_detector,
            snapshot_cb=self._on_appearance_requested if self.vlm_worker.enabled else None,
            multi_crop_cb=self._on_multi_crop_requested if self.vlm_worker.enabled else None,
            queue_check_cb=self._on_queue_check_requested if self.vlm_worker.enabled else None,
            scene_observe_cb=self._on_scene_observe_requested if self.vlm_worker.enabled else None,
        )

        # ---------- Orchestrator tick throttle ----------
        self._last_orchestrator_tick = 0.0
        # Per-frame tracked persons for orchestrator: {global_pid: info_dict}
        self._frame_tracked_persons: Dict[int, dict] = {}
        self._frame_shape: tuple = (720, 1280)

        # ---------- Display log ----------
        self._display_log: deque = deque(maxlen=40)

        # ---------- Web stream ----------
        self._latest_frame_jpeg: Optional[bytes] = None
        self._latest_frame_lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._camera_setup_thread: Optional[threading.Thread] = None
        self._monitor_start_event = threading.Event()
        self._monitor_stop_event = threading.Event()
        self._monitor_start_error: Optional[str] = None

        # ---------- Reliability / observability ----------
        self._start_time: float = time.time()
        self._consecutive_tick_errors: int = 0
        self._consecutive_frame_errors: int = 0
        self._tick_error_count_total: int = 0
        self._frame_error_count_total: int = 0
        self._last_heartbeat_time: float = 0.0
        self._heartbeat_interval_sec: float = 60.0

        # ---------- Cache persistence (survive watchdog restart) ----------
        import os as _os
        self._cache_snapshot_path: str = _os.environ.get(
            "PERSON_CACHE_SNAPSHOT_PATH",
            _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                "state", "person_cache.pkl",
            ),
        )
        self._cache_snapshot_interval_sec: float = 10.0
        self._last_cache_snapshot_time: float = 0.0
        self._last_cache_fingerprint: int = 0
        # Restore any cache from a previous run (TTL pruning runs on load)
        try:
            restored = self.person_cache.load_from_disk(self._cache_snapshot_path)
            if restored:
                logger.info(f"PersonCache restored: {restored} entries from previous run")
        except Exception as e:
            logger.warning(f"PersonCache restore skipped: {e}")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup_cameras(
        self,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        logger.info(f"Setting up {len(CAMERAS)} cameras...")

        for camera_id, cam_config in CAMERAS.items():
            if cancel_event is not None and cancel_event.is_set():
                return False
            source = cam_config["source"]
            resolution = cam_config.get("resolution", (1280, 720))

            success = self.camera_manager.register_camera(
                camera_id,
                source,
                resolution,
                cancel_event=cancel_event,
            )
            if not success:
                logger.warning(f"Skipping camera {camera_id} (failed to connect)")
                continue
            if cancel_event is not None and cancel_event.is_set():
                self.camera_manager.release_all()
                return False

            self.event_manager.register_camera(camera_id)

            cam_src = self.camera_manager.get_camera(camera_id)
            if cam_src is not None:
                self.snapshot_manager.register_camera(camera_id, cam_src.frame_ring_buffer)
                ann_buf: deque = deque(maxlen=TRACKING_CONFIG.get("annotated_buffer_size", 120))
                self._annotated_ring_buffers[camera_id] = ann_buf
                self.snapshot_manager.register_annotated_camera(camera_id, ann_buf)

            view_type = cam_config.get("view_type", "frontal")
            if view_type == "overhead":
                self.hand_detectors[camera_id] = OverheadHandDetector(**OVERHEAD_HAND_RAISE_CONFIG)
                logger.info(f"Camera {camera_id}: overhead hand detector")
            else:
                self.hand_detectors[camera_id] = HandDetector()
                logger.info(f"Camera {camera_id}: frontal hand detector")

            self._cam_tracker_states[camera_id] = []

        if (
            not self._cam_tracker_states
            or (cancel_event is not None and cancel_event.is_set())
        ):
            logger.error("No cameras available")
            return False

        self.vlm_worker.start()

        self.event_manager.register_callback(
            name="display_log",
            callback=self._on_event_for_display,
            events=None,
        )

        logger.info("All cameras setup successfully")
        return True

    # ------------------------------------------------------------------
    # Per-frame YOLO tracking
    # ------------------------------------------------------------------

    def process_frame(self, camera_id: str, frame: np.ndarray) -> np.ndarray:
        """Run YOLO detection + ReID + hand detection on one frame."""
        try:
            tracker_state = self._cam_tracker_states.get(camera_id)
            predictor = getattr(self.model, "predictor", None)
            if predictor is not None:
                if tracker_state:
                    predictor.trackers = tracker_state
                elif hasattr(predictor, "trackers"):
                    del predictor.trackers

            results = self.model.track(
                source=frame,
                persist=True,
                tracker="config/botsort.yaml",
                imgsz=YOLO_CONFIG["imgsz"],
                conf=YOLO_CONFIG["conf_threshold"],
                iou=YOLO_CONFIG["iou_threshold"],
                verbose=False,
            )

            if hasattr(self.model, "predictor") and self.model.predictor is not None:
                self._cam_tracker_states[camera_id] = self.model.predictor.trackers

            if not results or len(results) == 0:
                return frame

            annotated = frame.copy()
            r = results[0]
            boxes = r.boxes
            kpts = r.keypoints

            # TEMP DEBUG: log what YOLO is producing
            _dbg_n_boxes = 0 if boxes is None else len(boxes)
            _dbg_has_ids = boxes is not None and boxes.id is not None
            if not hasattr(self, "_yolo_dbg_tick"):
                self._yolo_dbg_tick = 0
            self._yolo_dbg_tick += 1
            if self._yolo_dbg_tick % 30 == 1:  # log once per ~1s at 30fps
                _confs_str = ""
                if boxes is not None and hasattr(boxes, "conf") and boxes.conf is not None:
                    try:
                        _confs_str = ", conf=" + str(
                            [round(float(c), 2) for c in boxes.conf.cpu().numpy().tolist()]
                        )
                    except Exception:
                        pass
                logger.info(
                    f"[YOLO_DBG] {camera_id} boxes={_dbg_n_boxes} "
                    f"has_track_ids={_dbg_has_ids}{_confs_str}"
                )

            if boxes is None or kpts is None or len(boxes) == 0 or kpts.xy is None:
                return annotated

            ids = boxes.id
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            kpts_xy_all = kpts.xy.cpu().numpy()
            kpts_cf_all = kpts.conf.cpu().numpy()

        except Exception as e:
            logger.error(f"YOLO inference error on {camera_id}: {e}")
            return frame

        self._frame_shape = frame.shape

        for i in range(len(xyxy)):
            try:
                if ids is None:
                    continue
                try:
                    local_track_id = int(ids[i].item())
                except (ValueError, AttributeError):
                    continue

                box = xyxy[i].astype(int)
                det_conf = float(confs[i])
                # Horizontal: use bbox center.
                # Vertical: zone_point_y_fraction picks where down the bbox to
                # test against zones (0.5 = body center). Center is used because
                # the feet are often occluded / out of frame at this camera angle.
                _y_frac = TRACKING_CONFIG.get("zone_point_y_fraction", 0.5)
                raw_cx = (box[0] + box[2]) // 2
                raw_cy = int(box[1] + (box[3] - box[1]) * _y_frac)
                person_center = (raw_cx, raw_cy)  # will be smoothed after global ID assigned

                # --- Per-person depth (RealSense only; None on RGB sources) ---
                # Sampled from the torso region of the bbox; see
                # CameraSource.sample_depth_in_bbox. Flows to event payloads
                # and the orchestrator-facing dict. No consumer uses this for
                # decisions yet — the field is provisioned for future work.
                cam_src = self.camera_manager.get_camera(camera_id)
                depth_m: Optional[float] = (
                    cam_src.sample_depth_in_bbox(box) if cam_src is not None else None
                )

                # --- ReID ---
                reid_key = (camera_id, local_track_id)
                self._reid_frame_count[reid_key] += 1
                frame_num = self._reid_frame_count[reid_key]
                is_unconfirmed = local_track_id not in self.event_manager.camera_track_mappings.get(camera_id, {})
                should_extract = is_unconfirmed or (frame_num % 10 == 1)

                kpt_cf = kpts_cf_all[i] if kpts_cf_all is not None else None
                quality = self.reid.compute_quality_score(box, frame.shape, kpt_cf)

                reid_feature = None
                if should_extract:
                    if is_unconfirmed or quality >= self.reid.MIN_QUALITY_SCORE:
                        reid_feature = self.reid.extract_feature(frame, box)
                        if reid_feature is not None and quality >= self.reid.MIN_QUALITY_SCORE:
                            self.reid.add_to_tracklet(camera_id, local_track_id, reid_feature, quality)

                tracklet_feat = self.reid.get_tracklet_feature(camera_id, local_track_id)
                match_feature = tracklet_feat if tracklet_feat is not None else reid_feature
                allow_create = frame_num >= self.MAX_PENDING_FRAMES

                global_person_id = self.event_manager.get_or_create_person(
                    camera_id, local_track_id,
                    reid_model=self.reid, reid_feature=match_feature,
                    allow_create=allow_create,
                    position=person_center,
                )

                if global_person_id is None:
                    cv2.rectangle(annotated, (box[0], box[1]), (box[2], box[3]), (128, 128, 128), 1)
                    cv2.putText(annotated, f"?({frame_num})",
                                (box[0], max(20, box[1] - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (128, 128, 128), 1)
                    continue

                # --- Centroid EMA smoothing ---
                alpha = self._centroid_ema_alpha
                if global_person_id in self._centroid_ema:
                    prev_x, prev_y = self._centroid_ema[global_person_id]
                    ema_x = int(alpha * raw_cx + (1 - alpha) * prev_x)
                    ema_y = int(alpha * raw_cy + (1 - alpha) * prev_y)
                else:
                    ema_x, ema_y = raw_cx, raw_cy
                self._centroid_ema[global_person_id] = (ema_x, ema_y)
                person_center = (ema_x, ema_y)

                # --- PERSON_ENTERED (low-level event, always fired) ---
                if global_person_id not in self._known_person_ids:
                    self._known_person_ids.add(global_person_id)
                    event_data: dict = {
                        "bbox": box.tolist(),
                        "frame_size": [frame.shape[1], frame.shape[0]],
                    }
                    if depth_m is not None:
                        event_data["depth_m"] = round(depth_m, 3)
                    crop_b64 = self._crop_person(frame, box)
                    if crop_b64:
                        event_data["person_crop_b64"] = crop_b64
                    self.event_manager.record_event(Event(
                        event_type=EventType.PERSON_ENTERED,
                        camera_id=camera_id,
                        person_id=global_person_id,
                        data=event_data,
                    ))
                    logger.info(f"Person {global_person_id} entered (camera: {camera_id})")

                # --- ReID gallery ---
                tracklet_feat = self.reid.get_tracklet_feature(camera_id, local_track_id)
                if tracklet_feat is not None:
                    self.reid.update_gallery(global_person_id, tracklet_feat, quality=quality)

                # --- Hand raise ---
                kpts_xy = kpts_xy_all[i]
                kpts_cf = kpts_cf_all[i]
                detector = self.hand_detectors.get(camera_id)
                if detector is None:
                    continue
                left_raised, right_raised, details = detector.detect_hand_raise(
                    kpts_xy, kpts_cf, track_id=global_person_id,
                )

                now = time.time()
                cooldown = HAND_RAISE_CONFIG["cooldown_sec"]
                last_trigger = self.last_raise_trigger[global_person_id]

                if last_trigger > 0 and (now - last_trigger) > cooldown:
                    ps = self.event_manager.get_person_state(global_person_id)
                    if ps is not None and ps.is_raising_hand:
                        ps.is_raising_hand = False
                        ps.raised_hand_side = None

                is_raising = False
                if left_raised or right_raised:
                    can_fire = (last_trigger == 0) or (now - last_trigger) > cooldown
                    if can_fire:
                        is_raising = True
                        self.last_raise_trigger[global_person_id] = now
                        side = "both" if (left_raised and right_raised) else ("left" if left_raised else "right")
                        hand_event_data = {
                            "side": side,
                            "confidence": det_conf,
                            "left_score": details["left"]["score"],
                            "right_score": details["right"]["score"],
                            "bbox": box.tolist(),
                        }
                        if depth_m is not None:
                            hand_event_data["depth_m"] = round(depth_m, 3)
                        crop_b64_hand = self._crop_person(frame, box)
                        if crop_b64_hand:
                            hand_event_data["person_crop_b64"] = crop_b64_hand
                        self.event_manager.record_event(Event(
                            event_type=EventType.HAND_RAISED,
                            camera_id=camera_id,
                            person_id=global_person_id,
                            data=hand_event_data,
                        ))
                        logger.info(f"Hand raised: person {global_person_id} [{side}] (camera: {camera_id})")

                self.event_manager.update_person_seen(global_person_id, camera_id, person_center)
                self.person_frame_count[global_person_id] += 1

                # --- Accumulate for orchestrator ---
                crop_b64_for_orch = None
                if global_person_id not in self._frame_tracked_persons:
                    # First time this person is seen this frame — include crop
                    # Always provide crop so the orchestrator can collect multi-frame history
                    crop_b64_for_orch = self._crop_person(frame, box)

                self._frame_tracked_persons[global_person_id] = {
                    "center": person_center,
                    "bbox": box.tolist(),
                    "frame_w": frame.shape[1],
                    "frame_h": frame.shape[0],
                    "is_raising_hand": is_raising,
                    "crop_b64": crop_b64_for_orch,
                    "depth_m": depth_m,
                    # Cumulative camera frames this global_id has been seen.
                    # Used by the orchestrator's entry commitment gate to reject
                    # transient YOLO/tracker glitches (a phantom never racks up frames).
                    "frame_count": self.person_frame_count[global_person_id],
                }

                self._draw_detection(annotated, box, global_person_id,
                                     left_raised, right_raised,
                                     details["left"]["score"], details["right"]["score"],
                                     det_conf, camera_id)

            except Exception as e:
                import traceback
                logger.error(f"Error processing person {i} on {camera_id}: {e}\n{traceback.format_exc()}")
                continue

        return annotated

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self, demo_mode: bool = True) -> None:
        stop_event = self._monitor_stop_event
        setup_done = threading.Event()
        setup_result: dict[str, object] = {}

        def setup() -> None:
            try:
                setup_result["ready"] = self.setup_cameras(cancel_event=stop_event)
            except Exception as exc:
                setup_result["error"] = exc
                setup_result["traceback"] = exc.__traceback__
            finally:
                if stop_event.is_set():
                    self._stop_capture()
                setup_done.set()

        setup_thread = threading.Thread(target=setup, daemon=True)
        self._camera_setup_thread = setup_thread
        setup_thread.start()
        while not setup_done.wait(timeout=0.05):
            if stop_event.is_set():
                if self._monitor_start_error is None:
                    self._monitor_start_error = "Camera startup was cancelled."
                self._monitor_start_event.set()
                self._stop_capture()
                return

        setup_error = setup_result.get("error")
        if isinstance(setup_error, Exception):
            self._monitor_start_error = f"Camera setup failed: {setup_error}"
            self._monitor_start_event.set()
            logger.error(
                self._monitor_start_error,
                exc_info=(
                    type(setup_error),
                    setup_error,
                    setup_result.get("traceback"),
                ),
            )
            self._stop_capture()
            return
        cameras_ready = bool(setup_result.get("ready"))
        if not cameras_ready:
            sources = ", ".join(
                f"{camera_id}={camera['source']}"
                for camera_id, camera in CAMERAS.items()
            )
            self._monitor_start_error = (
                "No camera source could be opened. "
                f"Configured source: {sources}. "
                "Choose a video file or start the configured MJPEG/RTSP stream."
            )
            self._monitor_start_event.set()
            logger.error("Failed to setup cameras")
            self._stop_capture()
            return

        if stop_event.is_set():
            if self._monitor_start_error is None:
                self._monitor_start_error = "Camera startup was cancelled."
            self._monitor_start_event.set()
            self._stop_capture()
            return

        self._monitor_start_error = None
        self._monitor_start_event.set()
        logger.info("Room monitoring started. Press 'q' to quit, 's' for statistics")
        self._start_time = time.time()

        # Hard limit on consecutive tick/frame errors before giving up. At
        # ~0.2s per tick, 300 errors is ~60 seconds of continuous failure.
        MAX_CONSECUTIVE_TICK_ERRORS = 300
        MAX_CONSECUTIVE_FRAME_ERRORS = 300

        try:
            while not stop_event.is_set():
                self._frame_tracked_persons.clear()

                try:
                    frames = self.camera_manager.read_all_frames()
                except Exception as e:
                    logger.error(f"read_all_frames raised: {e}")
                    frames = {}

                for camera_id, (ok, frame) in frames.items():
                    if not ok or frame is None:
                        continue

                    try:
                        annotated = self.process_frame(camera_id, frame)
                    except Exception as e:
                        self._frame_error_count_total += 1
                        self._consecutive_frame_errors += 1
                        logger.error(
                            f"process_frame failed on {camera_id} "
                            f"(consecutive={self._consecutive_frame_errors}): {e}"
                        )
                        if self._consecutive_frame_errors >= MAX_CONSECUTIVE_FRAME_ERRORS:
                            logger.critical(
                                "Too many consecutive frame errors — aborting main loop"
                            )
                            raise
                        continue
                    else:
                        self._consecutive_frame_errors = 0

                    if camera_id in self._annotated_ring_buffers:
                        self._annotated_ring_buffers[camera_id].append((time.time(), annotated))

                    # Draw health overlay on the copy that goes to web stream.
                    # This is the ONLY monitoring surface when no one is
                    # tailing logs — whoever opens the dashboard sees it.
                    try:
                        self._draw_health_overlay(annotated, camera_id)
                    except Exception as e:
                        logger.debug(f"Health overlay draw failed: {e}")

                    try:
                        _, jpg = cv2.imencode('.jpg', annotated,
                                             [cv2.IMWRITE_JPEG_QUALITY, 75])
                        with self._latest_frame_lock:
                            self._latest_frame_jpeg = jpg.tobytes()
                    except Exception:
                        pass

                    if demo_mode and VISUALIZATION["show_annotated_frames"]:
                        try:
                            fps = self.camera_manager.get_fps(camera_id)
                            cv2.putText(annotated, f"{camera_id} | FPS: {fps:.1f}",
                                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                            self._draw_zones(annotated)
                            self._draw_cache_states(annotated)
                            log_panel = self._render_log_panel(annotated.shape[0])
                            combined = np.hstack([annotated, log_panel])
                            cv2.imshow(f"RoomMonitor_{camera_id}", combined)
                        except Exception as e:
                            logger.warning(f"Display render failed on {camera_id}: {e}")

                # --- Orchestrator tick (throttled, protected) ---
                now = time.time()
                if now - self._last_orchestrator_tick >= _ORCHESTRATOR_INTERVAL:
                    self._last_orchestrator_tick = now
                    try:
                        self.orchestrator.tick(self._frame_tracked_persons)
                        self._run_person_cleanup()
                        self._consecutive_tick_errors = 0
                    except Exception as e:
                        self._tick_error_count_total += 1
                        self._consecutive_tick_errors += 1
                        logger.error(
                            f"Orchestrator tick failed "
                            f"(consecutive={self._consecutive_tick_errors}): {e}",
                            exc_info=True,
                        )
                        if self._consecutive_tick_errors >= MAX_CONSECUTIVE_TICK_ERRORS:
                            logger.critical(
                                "Too many consecutive tick errors — aborting main loop"
                            )
                            raise

                # --- Periodic heartbeat log (DEBUG level, no-one reads it) ---
                if now - self._last_heartbeat_time >= self._heartbeat_interval_sec:
                    self._last_heartbeat_time = now
                    try:
                        self._emit_heartbeat(now)
                    except Exception as e:
                        logger.debug(f"Heartbeat emit failed: {e}")

                # --- Periodic PersonCache snapshot to disk ---
                if now - self._last_cache_snapshot_time >= self._cache_snapshot_interval_sec:
                    self._last_cache_snapshot_time = now
                    try:
                        fp = self.person_cache.fingerprint()
                        if fp != self._last_cache_fingerprint:
                            if self.person_cache.save_to_disk(self._cache_snapshot_path):
                                self._last_cache_fingerprint = fp
                    except Exception as e:
                        logger.debug(f"PersonCache snapshot failed: {e}")

                if demo_mode and VISUALIZATION["show_annotated_frames"]:
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        logger.info("Quit requested")
                        break
                    elif key == ord("s"):
                        self._print_statistics()
                else:
                    time.sleep(0.01)

        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")
        finally:
            self._stop_capture()

    def _emit_heartbeat(self, now: float) -> None:
        """Structured single-line heartbeat — DEBUG level only.

        Kept for post-mortem log forensics but not emitted at INFO since no
        one is tailing logs in production. Enable with `logging.DEBUG`.
        """
        if not logger.isEnabledFor(logging.DEBUG):
            return

        cams = self.camera_manager.get_camera_list()
        fps_str = ",".join(
            f"{cid}={self.camera_manager.get_fps(cid):.1f}" for cid in cams
        )
        reconnects = sum(
            getattr(self.camera_manager.get_camera(cid), "reconnect_count", 0)
            for cid in cams
        )

        vlm_q = self.vlm_worker.queue_depth() if hasattr(self.vlm_worker, "queue_depth") else -1
        vlm_drop = self.vlm_worker.dropped_count() if hasattr(self.vlm_worker, "dropped_count") else -1
        vlm_err = self.vlm_worker.error_count() if hasattr(self.vlm_worker, "error_count") else -1
        vlm_calls = self.vlm_worker.call_count() if hasattr(self.vlm_worker, "call_count") else -1
        vlm_brk = self.vlm_worker.breaker_open() if hasattr(self.vlm_worker, "breaker_open") else False

        pub_ok = self.event_publisher.published_count() if hasattr(self.event_publisher, "published_count") else -1
        pub_fail = self.event_publisher.failed_count() if hasattr(self.event_publisher, "failed_count") else -1
        pub_pending = self.event_publisher.pending_count() if hasattr(self.event_publisher, "pending_count") else -1

        ram_mb = self._get_rss_mb()
        uptime = int(now - self._start_time)
        room = self.person_cache.room_person_count()

        logger.debug(
            "HEARTBEAT "
            f"uptime={uptime}s "
            f"fps=[{fps_str}] "
            f"reconnects={reconnects} "
            f"room={room} "
            f"vlm_q={vlm_q} vlm_drop={vlm_drop} vlm_err={vlm_err}/{vlm_calls} vlm_brk={vlm_brk} "
            f"pub_ok={pub_ok} pub_fail={pub_fail} pub_pending={pub_pending} "
            f"tick_err={self._tick_error_count_total} frame_err={self._frame_error_count_total} "
            f"ram_mb={ram_mb}"
        )

    @staticmethod
    def _get_rss_mb() -> int:
        """Return process RSS in MB. Uses psutil if available, else resource."""
        try:
            import psutil
            return int(psutil.Process().memory_info().rss / (1024 * 1024))
        except Exception:
            try:
                import resource
                rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                # macOS returns bytes, Linux returns KB
                import sys as _sys
                if _sys.platform == "darwin":
                    return int(rss_kb / (1024 * 1024))
                return int(rss_kb / 1024)
            except Exception:
                return -1

    # ------------------------------------------------------------------
    # Orchestrator callbacks
    # ------------------------------------------------------------------

    def _on_event_for_display(self, event) -> None:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        if isinstance(event, dict):
            et = event.get("event_type", "?")
            pid = event.get("person_id", "?")
        else:
            et = event.event_type.value
            pid = event.person_id
        self._display_log.append((ts, "EVT", f"{et} P{pid}"))

    def _on_orchestrator_event(self, event_type: str, payload: dict) -> None:
        self.event_publisher.publish(event_type, "", payload)
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        pid = payload.get("person_id", payload.get("person_ids", ""))
        self._display_log.append((ts, "ORCH", f"{event_type} P{pid}"))

    def _on_appearance_requested(self, person_id: int, crop_b64: Optional[str]) -> None:
        """Orchestrator requests VLM appearance for a newly entered person."""
        if crop_b64 is None:
            return
        self.vlm_worker.request_person_appearance_b64(person_id, crop_b64)

    def _on_multi_crop_requested(self, person_id: int, crop_b64_list: list[str]) -> None:
        """Orchestrator requests multi-frame VLM re-analysis (e.g. on mic zone entry)."""
        if not crop_b64_list:
            return
        self.vlm_worker.request_person_appearance_multi(person_id, crop_b64_list)

    def _on_queue_check_requested(self, result_cb) -> None:
        """Orchestrator asks whether someone is waiting behind the mic person.

        Grabs the latest full frame (the queue may be anywhere in view, not just
        in one person's crop), base64-encodes it, and hands it to the VLM. The
        result bool is delivered async via result_cb. If no frame is available
        yet, report 'not waiting' so the caller never wedges.
        """
        jpeg = self.get_latest_frame_jpeg()
        if not jpeg:
            result_cb(False)
            return
        import base64
        frame_b64 = base64.b64encode(jpeg).decode("ascii")
        self.vlm_worker.request_queue_check(frame_b64, result_cb)

    def _on_scene_observe_requested(self, result_cb) -> None:
        """Orchestrator asks for a periodic full-frame environment observation.

        Grabs the latest full frame, base64-encodes it, and hands it to the VLM.
        Result dict ({description, queue_behind}) is delivered async via
        result_cb; an empty dict if no frame is available (caller falls back to
        geometry)."""
        jpeg = self.get_latest_frame_jpeg()
        if not jpeg:
            result_cb({})
            return
        import base64
        frame_b64 = base64.b64encode(jpeg).decode("ascii")
        self.vlm_worker.request_scene_observation(frame_b64, result_cb)

    def _on_person_appearance(self, person_id: int, result: dict) -> None:
        """VLM returned appearance description — store in person cache."""
        self.person_cache.set_appearance(person_id, result)
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        top = result.get("top", "")
        bottom = result.get("bottom", "")
        notable = result.get("notable", "")
        if top:
            self._display_log.append((ts, "VLM", f"P{person_id} top: {top}"))
        if bottom:
            self._display_log.append((ts, "VLM", f"P{person_id} bottom: {bottom}"))
        if notable and notable.lower() != "none":
            self._display_log.append((ts, "VLM", f"P{person_id} notable: {notable}"))
        logger.info(f"Person {person_id} appearance: {result}")

    # ------------------------------------------------------------------
    # Visualization helpers
    # ------------------------------------------------------------------

    def _draw_health_overlay(self, frame: np.ndarray, camera_id: str) -> None:
        """Draw a compact health status bar in the top-right of the frame.

        This is the ONLY monitoring surface in "nobody watches logs" mode.
        Whoever glances at the dashboard feed sees three colored dots:
          ● CAM — green if this camera is open, reads are fresh, and fps ≥ 5
                    after an initial two-second measurement window
          ● VLM — green if enabled & breaker closed, yellow if disabled, red if breaker open
          ● EVT — green if publisher.failed_count didn't increase recently

        A red banner at the top screams if ANY of them is red.
        """
        h, w = frame.shape[:2]
        now = time.time()

        # --- CAM status ---
        cam = self.camera_manager.get_camera(camera_id)
        cam_ok = False
        cam_detail = "?"
        if cam is not None:
            fps = self.camera_manager.get_fps(camera_id)
            last_read_age = now - getattr(cam, "last_successful_read_time", 0.0)
            reconnects = getattr(cam, "reconnect_count", 0)
            warming_up = now - self._start_time < 2.0
            cam_ok = (
                getattr(cam, "is_opened", False)
                and last_read_age < 2.0
                and (fps >= 5.0 or warming_up)
            )
            cam_detail = "warmup" if warming_up and fps < 5.0 else f"{fps:.0f}fps"
            if reconnects:
                cam_detail += f" rc={reconnects}"

        # --- VLM status ---
        vlm_enabled = getattr(self.vlm_worker, "enabled", False)
        vlm_broken = (
            hasattr(self.vlm_worker, "breaker_open") and self.vlm_worker.breaker_open()
        )
        if not vlm_enabled:
            vlm_color = (128, 128, 128)  # gray — disabled
            vlm_ok = True  # not a failure
            vlm_detail = "off"
        elif vlm_broken:
            vlm_color = (0, 0, 255)      # red — breaker open
            vlm_ok = False
            q = self.vlm_worker.queue_depth() if hasattr(self.vlm_worker, "queue_depth") else 0
            vlm_detail = f"BROKEN q={q}"
        else:
            vlm_color = (0, 200, 0)      # green
            vlm_ok = True
            q = self.vlm_worker.queue_depth() if hasattr(self.vlm_worker, "queue_depth") else 0
            d = self.vlm_worker.dropped_count() if hasattr(self.vlm_worker, "dropped_count") else 0
            vlm_detail = f"q={q} d={d}"

        # --- EVT status: track delta since last overlay draw ---
        pub_failed = (
            self.event_publisher.failed_count()
            if hasattr(self.event_publisher, "failed_count") else 0
        )
        last_failed = getattr(self, "_overlay_last_pub_failed", 0)
        evt_ok = pub_failed == last_failed
        self._overlay_last_pub_failed = pub_failed
        evt_color = (0, 200, 0) if evt_ok else (0, 0, 255)
        pub_ok_cnt = (
            self.event_publisher.published_count()
            if hasattr(self.event_publisher, "published_count") else 0
        )
        evt_detail = f"{pub_ok_cnt}ok/{pub_failed}fail"

        # --- Draw three status pills ---
        pill_w = 140
        pill_h = 24
        gap = 6
        x0 = w - (pill_w * 3 + gap * 2) - 10
        y0 = 8

        def _pill(x, label, color, detail):
            cv2.rectangle(frame, (x, y0), (x + pill_w, y0 + pill_h),
                          (20, 20, 20), cv2.FILLED)
            cv2.circle(frame, (x + 10, y0 + pill_h // 2), 6, color, cv2.FILLED)
            cv2.putText(frame, f"{label} {detail}",
                        (x + 22, y0 + pill_h - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1)

        _pill(x0, "CAM", (0, 200, 0) if cam_ok else (0, 0, 255), cam_detail)
        _pill(x0 + pill_w + gap, "VLM", vlm_color, vlm_detail)
        _pill(x0 + (pill_w + gap) * 2, "EVT", evt_color, evt_detail)

        # --- Red alert banner if anything is broken ---
        any_broken = (not cam_ok) or (not vlm_ok) or (not evt_ok)
        if any_broken:
            banner_h = 28
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, banner_h), (0, 0, 180), cv2.FILLED)
            cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
            broken_parts = []
            if not cam_ok:
                broken_parts.append("CAMERA")
            if not vlm_ok:
                broken_parts.append("VLM")
            if not evt_ok:
                broken_parts.append("EVENTS")
            msg = f"!! SYSTEM DEGRADED — {' '.join(broken_parts)} !!"
            cv2.putText(frame, msg, (12, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    def _draw_zones(self, frame: np.ndarray) -> None:
        """Draw entry zone and mic zone polygons on the frame."""
        h, w = frame.shape[:2]
        for zone_name, color in [("entry_zone", (0, 200, 255)), ("mic_zone", (255, 100, 255))]:
            pts = self.zone_detector.get_zone_polygon_pixels(zone_name, w, h)
            if pts:
                pts_arr = np.array(pts, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(frame, [pts_arr], True, color, 2)
                cv2.putText(frame, zone_name, (pts[0][0] + 5, pts[0][1] + 15),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    def _draw_cache_states(self, frame: np.ndarray) -> None:
        """Draw person cache state info at top of frame."""
        y = 60
        for entry in self.person_cache.get_all_in_room():
            state_color = {
                "idle": (200, 200, 200),
                "in_mic_zone": (0, 255, 0),
                "hand_raised": (0, 0, 255),
            }.get(entry.scene_state, (200, 200, 200))
            text = f"P{entry.person_id}: {entry.scene_state}"
            if entry.invited:
                text += f" [inv:{entry.invite_batch_id}]"
            if entry.appearance_ready:
                text += " [VLM OK]"
            cv2.putText(frame, text, (10, y),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, state_color, 1)
            y += 18

    def _render_log_panel(self, height: int, width: int = 420) -> np.ndarray:
        panel = np.zeros((height, width, 3), dtype=np.uint8)
        cv2.putText(panel, "System Events", (10, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
        cv2.line(panel, (0, 30), (width, 30), (60, 60, 60), 1)

        color_map = {
            "EVT":  (190, 190, 190),
            "ORCH": (80, 255, 140),
            "VLM":  (255, 190, 80),
        }
        line_h = 17
        max_lines = (height - 40) // line_h
        entries = list(self._display_log)[-max_lines:]
        for i, (ts, tag, msg) in enumerate(entries):
            y = 48 + i * line_h
            color = color_map.get(tag, (200, 200, 200))
            text = f"[{ts}][{tag}] {msg}"[:62]
            cv2.putText(panel, text, (6, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, color, 1)
        return panel

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_person_cleanup(self) -> None:
        removed_ids = self.event_manager.cleanup_old_person_states(
            person_timeout_sec=PERSON_CONFIG.get("person_timeout_sec", 30),
            cleanup_delay_sec=PERSON_CONFIG.get("cleanup_delay_sec", 5),
        )
        for pid in removed_ids:
            self.reid.remove_person(pid)
            self.last_raise_trigger.pop(pid, None)
            self.person_frame_count.pop(pid, None)
            self._known_person_ids.discard(pid)
            self._centroid_ema.pop(pid, None)
            self.person_cache.remove(pid)
            for det in self.hand_detectors.values():
                det.clear_track_state(pid)
            for cam_id, track_map in self.event_manager.camera_track_mappings.items():
                for tid, gid in list(track_map.items()):
                    if gid == pid:
                        self.reid.remove_tracklet(cam_id, tid)
                        self._reid_frame_count.pop((cam_id, tid), None)

        # Periodic cleanup of stale tracking keys not tied to active persons
        active_pids = set(self.event_manager.person_states.keys())
        for key in list(self._reid_frame_count.keys()):
            cam_id, local_tid = key
            track_map = self.event_manager.camera_track_mappings.get(cam_id, {})
            if local_tid not in track_map:
                self._reid_frame_count.pop(key, None)

    # UX state → (BGR color, label) for dashboard badges.
    # One distinct color per state so demos / debugging can visually verify
    # the orchestrator reacted correctly to each interaction signal.
    _UX_STATE_STYLE = {
        "HAND_UP":     ((0,   255,   0), "HAND_UP"),     # green
        "AT_MIC":      ((255, 100, 255), "AT MIC"),       # magenta
        "SEATED":      ((180, 220, 180), "SEATED"),       # pale green
        "NOTICED":     ((255, 255, 255), "NOTICED"),      # white
    }

    def _draw_detection(self, frame: np.ndarray, box: np.ndarray,
                        person_id: int, left_raised: bool, right_raised: bool,
                        left_score: float, right_score: float,
                        conf: float, camera_id: str) -> None:
        cache_entry = self.person_cache.get(person_id)

        # --- Box color: reflect ux_state if we have one, else fall back ---
        if cache_entry is not None:
            color, ux_label = self._UX_STATE_STYLE.get(
                cache_entry.ux_state,
                self._UX_STATE_STYLE["NOTICED"],
            )
        else:
            color = (128, 128, 128)
            ux_label = "PENDING"

        cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), color, 2)

        # --- Hand-raise inline flag ---
        status = []
        if left_raised:
            status.append("L_UP")
        if right_raised:
            status.append("R_UP")
        status_txt = ",".join(status) if status else ""

        # --- ID + ux_state badge above the bbox ---
        badge_text = f"P{person_id} {ux_label}"
        if status_txt:
            badge_text += f" {status_txt}"

        # Demographics hint (only if VLM has classified)
        if cache_entry is not None and cache_entry.age_group not in (None, "unknown", "adult"):
            badge_text += f" ({cache_entry.age_group})"

        (tw, th), _ = cv2.getTextSize(
            badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
        )
        badge_x = box[0]
        badge_y = max(th + 8, box[1] - 6)
        # Opaque pill background so the text is readable over any scene
        cv2.rectangle(
            frame,
            (badge_x - 2, badge_y - th - 4),
            (badge_x + tw + 6, badge_y + 4),
            (20, 20, 20),
            cv2.FILLED,
        )
        # Colored left border matches the ux_state color
        cv2.rectangle(
            frame,
            (badge_x - 2, badge_y - th - 4),
            (badge_x + 2, badge_y + 4),
            color,
            cv2.FILLED,
        )
        cv2.putText(
            frame, badge_text, (badge_x + 6, badge_y),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1,
        )

    def _crop_person(self, frame: np.ndarray, bbox) -> Optional[str]:
        if not AGENT_SERVER_CONFIG.get("include_person_crop", True):
            return None
        return encode_crop(frame, bbox,
                           jpeg_quality=AGENT_SERVER_CONFIG.get("crop_jpeg_quality", 75),
                           max_side=VLM_CONFIG.get("crop_max_side", 0))

    def _print_statistics(self) -> None:
        logger.info("=" * 50)
        summary = self.event_manager.get_room_summary()
        logger.info(f"Total persons: {summary['total_persons_in_room']}")
        logger.info(f"Raising hands: {summary['persons_raising_hands']}")
        logger.info(f"Recent events (10s): {summary['recent_events_count']}")
        logger.info(f"Person cache entries: {self.person_cache.room_person_count()}")
        for entry in self.person_cache.get_all_in_room():
            logger.info(
                f"  P{entry.person_id}: state={entry.scene_state} "
                f"invited={entry.invited} mic_visits={entry.mic_zone_visits} "
                f"appearance={entry.appearance_ready}"
            )
        logger.info("=" * 50)

    @property
    def is_running(self) -> bool:
        return self._monitor_thread is not None and self._monitor_thread.is_alive()

    @property
    def start_error(self) -> Optional[str]:
        return self._monitor_start_error

    def start_async(self, demo_mode: bool = False) -> bool:
        if self.is_running:
            return False
        if (
            self._camera_setup_thread is not None
            and self._camera_setup_thread.is_alive()
        ):
            self._monitor_start_error = (
                "Previous camera setup cancellation is still finishing."
            )
            return False
        self._monitor_start_error = None
        self._monitor_start_event.clear()
        self._monitor_stop_event = threading.Event()
        self._monitor_thread = threading.Thread(
            target=self.run, kwargs={"demo_mode": demo_mode}, daemon=True
        )
        self._monitor_thread.start()
        logger.info("Monitor started via start_async()")
        timeout = float(os.getenv("CAMERA_START_TIMEOUT_SECONDS", "10"))
        if not self._monitor_start_event.wait(timeout=timeout):
            self._monitor_start_error = (
                f"Camera setup did not complete within {timeout:.1f} seconds."
            )
            self._monitor_stop_event.set()
            logger.error(self._monitor_start_error)
            self._monitor_thread.join()
            return False
        if self._monitor_start_error is not None:
            self._monitor_thread.join()
            return False
        return self.is_running and self._monitor_start_error is None

    def stop_async(self, timeout: float = 5.0) -> bool:
        """Stop camera capture while keeping API, VLM, and event services reusable."""
        if not self.is_running:
            self._monitor_start_error = None
            self._clear_latest_frame()
            return True

        self._monitor_stop_event.set()
        self.camera_manager.release_all()
        self._monitor_thread.join(timeout=timeout)
        if self.is_running:
            self._monitor_start_error = (
                f"Camera monitor did not stop within {timeout:.1f} seconds."
            )
            logger.error(self._monitor_start_error)
            return False

        self._monitor_start_error = None
        self._clear_latest_frame()
        return True

    def _clear_latest_frame(self) -> None:
        with self._latest_frame_lock:
            self._latest_frame_jpeg = None

    def _stop_capture(self) -> None:
        self.camera_manager.release_all()
        self.hand_detectors.clear()
        self._cam_tracker_states.clear()
        self._annotated_ring_buffers.clear()
        predictor = getattr(self.model, "predictor", None)
        if predictor is not None and hasattr(predictor, "trackers"):
            del predictor.trackers
        self._clear_latest_frame()
        cv2.destroyAllWindows()
        logger.info("Camera capture stopped")

    def get_display_log(self) -> list:
        """Return a copy of the display log for external consumers."""
        return list(self._display_log)

    def get_latest_frame_jpeg(self) -> Optional[bytes]:
        with self._latest_frame_lock:
            return self._latest_frame_jpeg

    def get_raw_snapshot_jpeg(self) -> Optional[bytes]:
        """Latest *unannotated* camera frame as JPEG, for the zone labeler.

        Reads from the running camera's raw ring buffer. Returns None if the
        monitor isn't running / no frame is available yet — callers can then
        fall back to opening the configured source directly.
        """
        try:
            for cam_id in self.camera_manager.get_camera_list():
                cam = self.camera_manager.get_camera(cam_id)
                if cam is not None and len(cam.frame_ring_buffer) > 0:
                    _ts, frame = cam.frame_ring_buffer[-1]
                    ok, buf = cv2.imencode(
                        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
                    )
                    if ok:
                        return buf.tobytes()
        except Exception as e:
            logger.debug(f"get_raw_snapshot_jpeg failed: {e}")
        return None

    def shutdown(self) -> None:
        logger.info("Shutting down...")
        # Final cache snapshot so next boot can resume without loss
        try:
            self.person_cache.save_to_disk(self._cache_snapshot_path)
        except Exception as e:
            logger.debug(f"Final PersonCache snapshot failed: {e}")
        self.vlm_worker.stop()
        self.event_publisher.shutdown(wait=True)
        self._stop_capture()
        logger.info("System shutdown complete")
