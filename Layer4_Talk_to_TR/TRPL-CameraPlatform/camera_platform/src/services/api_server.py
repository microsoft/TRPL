# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Room monitoring system REST API service
Supports remote querying and control
"""
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from werkzeug.utils import secure_filename
import threading
import time
import logging
import os
from .web_dashboard import add_web_dashboard
from .zone_label_page import ZONE_LABEL_HTML
from .ws_monitor_page import WS_MONITOR_HTML

logger = logging.getLogger(__name__)


class RoomMonitorAPI:
    """Room monitoring system API service"""

    def __init__(self, monitor, host: str = "0.0.0.0", port: int = 5000, debug: bool = False):
        """
        Args:
            monitor: RoomMonitor instance
            host: Service bind address
            port: Service port
            debug: Whether to enable debug mode
        """
        self.monitor = monitor
        self.host = host
        self.port = port
        self.debug = debug
        self._configured_camera_sources = {}
        try:
            from ..utils.config import CAMERAS
            self._configured_camera_sources = {
                camera_id: camera["source"]
                for camera_id, camera in CAMERAS.items()
            }
        except (ImportError, KeyError, TypeError):
            logger.exception("Unable to preserve configured camera sources")

        self.app = Flask(__name__)
        CORS(self.app)  # Enable CORS

        # Add web dashboard
        add_web_dashboard(self.app)

        self._register_routes()

    def _register_routes(self):
        """Register all API routes"""

        @self.app.route("/api/health", methods=["GET"])
        def health():
            """Minimal health check. Runtime metrics live on the video
            overlay (top-right pills) — the only surface anyone actually
            looks at. No one tails JSON from /health in production."""
            return jsonify({
                "status": "ok",
                "timestamp": time.time(),
            })

        @self.app.route("/api/room/summary", methods=["GET"])
        def room_summary():
            """Get current room status summary"""
            try:
                summary = self.monitor.event_manager.get_room_summary()
                return jsonify(summary)
            except Exception as e:
                logger.error(f"Error getting room summary: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/room/persons", methods=["GET"])
        def room_persons():
            """Get details of all persons in the room"""
            try:
                persons = self.monitor.event_manager.get_persons_in_room()
                return jsonify({
                    "count": len(persons),
                    "persons": [p.to_dict() for p in persons]
                })
            except Exception as e:
                logger.error(f"Error getting persons: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/person/<int:person_id>", methods=["GET"])
        def person_detail(person_id):
            """Get detailed info for a single person"""
            try:
                person = self.monitor.event_manager.get_person_state(person_id)
                if not person:
                    return jsonify({"error": "Person not found"}), 404
                return jsonify(person.to_dict())
            except Exception as e:
                logger.error(f"Error getting person detail: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/events", methods=["GET"])
        def get_events():
            """Get event list"""
            try:
                event_type = request.args.get("type")
                person_id = request.args.get("person_id", type=int)
                camera_id = request.args.get("camera_id")
                time_window = request.args.get("time_window", type=float)

                events = self.monitor.event_manager.get_events(
                    event_type=event_type,
                    person_id=person_id,
                    camera_id=camera_id,
                    time_window=time_window
                )

                return jsonify({
                    "count": len(events),
                    "events": [e.to_dict() for e in events]
                })
            except Exception as e:
                logger.error(f"Error getting events: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/cameras", methods=["GET"])
        def cameras():
            """Get all camera info"""
            try:
                cam_list = self.monitor.camera_manager.get_camera_list()
                cameras_info = []
                for cam_id in cam_list:
                    cam = self.monitor.camera_manager.get_camera(cam_id)
                    if cam:
                        cameras_info.append({
                            "camera_id": cam_id,
                            "fps": self.monitor.camera_manager.get_fps(cam_id),
                            "is_opened": cam.is_opened,
                            "resolution": cam.resolution if cam else None,
                        })
                return jsonify({
                    "count": len(cameras_info),
                    "cameras": cameras_info
                })
            except Exception as e:
                logger.error(f"Error getting cameras: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/analyze", methods=["POST"])
        def analyze():
            """Trigger LLM analysis"""
            try:
                summary = self.monitor.event_manager.get_room_summary()
                analysis = self.monitor.llm_agent.analyze_room_state(summary)

                if not analysis:
                    return jsonify({
                        "error": "LLM analysis failed (API key not configured?)"
                    }), 500

                return jsonify({
                    "timestamp": time.time(),
                    "analysis": analysis,
                    "summary": summary
                })
            except Exception as e:
                logger.error(f"Error in analysis: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/question", methods=["POST"])
        def answer_question():
            """Answer questions about room status"""
            try:
                data = request.get_json()
                if not data or "question" not in data:
                    return jsonify({"error": "Missing 'question' field"}), 400

                question = data["question"].strip()
                if not question or len(question) > 2000:
                    return jsonify({"error": "Question must be 1-2000 characters"}), 400
                context = self.monitor.event_manager.get_room_summary()

                answer = self.monitor.llm_agent.answer_question(question, context)

                if not answer:
                    return jsonify({
                        "error": "Failed to answer question"
                    }), 500

                return jsonify({
                    "question": question,
                    "answer": answer,
                    "timestamp": time.time(),
                })
            except Exception as e:
                logger.error(f"Error answering question: {e}")
                return jsonify({"error": str(e)}), 500

        # ---- Video Stream ----

        @self.app.route("/video_feed")
        def video_feed():
            """MJPEG stream of the latest annotated frame."""
            def gen():
                while True:
                    jpg = self.monitor.get_latest_frame_jpeg()
                    if jpg is not None:
                        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                               + jpg + b'\r\n')
                    time.sleep(0.033)
            return Response(gen(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

        # ---- WebSocket Event Monitor ----

        @self.app.route("/monitor")
        def ws_monitor_page():
            """Live, read-only view of events pushed over the WS stream."""
            return Response(WS_MONITOR_HTML, mimetype="text/html")

        @self.app.route("/api/events/recent")
        def events_recent():
            """In-memory tap of recently-emitted event envelopes (poll cursor
            via ?after=<seq>). Does NOT touch the consume-once WS stack."""
            try:
                after = int(request.args.get("after", 0))
            except (TypeError, ValueError):
                after = 0
            return jsonify(self.monitor.event_publisher.recent_events(after_seq=after))

        # ---- Zone Labeler ----

        @self.app.route("/label")
        def zone_labeler_page():
            """Interactive web page to draw entry/speaking zones on a snapshot."""
            return Response(ZONE_LABEL_HTML, mimetype="text/html")

        @self.app.route("/api/snapshot")
        def snapshot():
            """A single unannotated JPEG of the current camera view.

            Prefers the running monitor's raw frame; falls back to opening the
            configured source directly so labeling works before Start is pressed.
            """
            jpg = self.monitor.get_raw_snapshot_jpeg()
            if jpg is None:
                jpg = self._grab_snapshot_from_source()
            if jpg is None:
                return jsonify({"error": "no frame available (camera connected?)"}), 503
            return Response(jpg, mimetype="image/jpeg",
                            headers={"Cache-Control": "no-store"})

        @self.app.route("/api/zones", methods=["GET"])
        def get_zones():
            """Current active zones as {name: [[x,y],...]} normalized, plus the
            optional entry_direction arrow so the labeler can preload it."""
            zones = self.monitor.zone_detector.get_all_zones_norm()
            direction = self.monitor.zone_detector.get_entry_direction()
            if direction is not None:
                zones["entry_direction"] = direction
            return jsonify(zones)

        @self.app.route("/api/zones", methods=["POST"])
        def set_zones():
            """Persist labeled zones and live-reload them into the monitor."""
            data = request.get_json(silent=True) or {}
            from ..utils import zone_store
            try:
                cleaned = zone_store.normalize_zones(data)
                if not cleaned:
                    return jsonify({"error": "No valid zone (each needs >= 3 vertices in 0..1)"}), 400
                zone_store.save_persisted_zones(cleaned)
                applied = self.monitor.zone_detector.reload_zones(cleaned)
                logger.info(f"Zones updated via /api/zones: {applied}")
                return jsonify({"status": "ok", "applied": applied})
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            except Exception as e:
                logger.error(f"Failed to set zones: {e}", exc_info=True)
                return jsonify({"error": str(e)}), 500

        # ---- Monitor Control ----

        @self.app.route("/api/control/upload", methods=["POST"])
        def control_upload():
            """Accept a video file upload and set it as the camera source."""
            if self.monitor.is_running:
                return jsonify({"error": "Monitor already running"}), 400
            if "video" not in request.files:
                return jsonify({"error": "No video field in request"}), 400
            f = request.files["video"]
            if not f.filename:
                return jsonify({"error": "Empty filename"}), 400
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
                return jsonify({"error": f"Unsupported format: {ext}"}), 400
            os.makedirs("test_data", exist_ok=True)
            safe_name = secure_filename(f"upload_tmp{ext}")
            save_path = os.path.join("test_data", safe_name)
            f.save(save_path)
            # Update camera source in config
            import src.utils.config as _cfg
            for cam in _cfg.CAMERAS.values():
                cam["source"] = save_path
            logger.info(f"Video uploaded and set as source: {save_path}")
            return jsonify({"status": "uploaded", "path": save_path})

        @self.app.route("/api/control/start", methods=["POST"])
        def control_start():
            """Start the monitor (triggered by Start button)."""
            if self.monitor.is_running:
                return jsonify({"status": "already_running"})
            if not self.monitor.start_async(demo_mode=False):
                return jsonify({
                    "status": "error",
                    "error": self.monitor.start_error or "Camera monitor failed to start",
                }), 503
            return jsonify({"status": "started"})

        @self.app.route("/api/control/stop", methods=["POST"])
        def control_stop():
            """Stop capture without shutting down the API or background services."""
            if not self.monitor.stop_async():
                return jsonify({
                    "status": "error",
                    "error": self.monitor.start_error or "Camera monitor failed to stop",
                }), 503
            return jsonify({"status": "stopped"})

        @self.app.route("/api/control/use-configured-source", methods=["POST"])
        def control_use_configured_source():
            """Switch from an uploaded video back to the deployment camera source."""
            if not self.monitor.stop_async():
                return jsonify({
                    "status": "error",
                    "error": self.monitor.start_error or "Camera monitor failed to stop",
                }), 503

            from ..utils.config import CAMERAS
            for camera_id, source in self._configured_camera_sources.items():
                if camera_id in CAMERAS:
                    CAMERAS[camera_id]["source"] = source

            if not self.monitor.start_async(demo_mode=False):
                return jsonify({
                    "status": "error",
                    "error": self.monitor.start_error or "Configured camera failed to start",
                }), 503
            return jsonify({"status": "started"})

        @self.app.route("/api/control/status", methods=["GET"])
        def control_status():
            return jsonify({
                "running": self.monitor.is_running,
                "error": self.monitor.start_error,
            })

        # ---- Display Log ----

        @self.app.route("/api/display_log", methods=["GET"])
        def display_log():
            """Return recent system events for the web log panel."""
            entries = [
                {"ts": ts, "tag": tag, "msg": msg}
                for ts, tag, msg in self.monitor.get_display_log()
            ]
            return jsonify(entries)

        # ---- Webhook Management API ----

        @self.app.route("/api/webhooks", methods=["GET"])
        def list_webhooks():
            """List all registered webhooks"""
            return jsonify(self.monitor.event_manager.list_webhooks())

        @self.app.route("/api/webhooks", methods=["POST"])
        def register_webhook():
            """
            Register webhook. Body:
            {"name": "my_hook", "url": "http://...", "events": ["hand_raised"], "secret": "xxx"}
            """
            try:
                data = request.get_json()
                if not data or "name" not in data or "url" not in data:
                    return jsonify({"error": "Missing 'name' and 'url' fields"}), 400

                url = data["url"]
                if not url.startswith(("http://", "https://")):
                    return jsonify({"error": "URL must start with http:// or https://"}), 400

                self.monitor.event_manager.register_webhook(
                    name=data["name"],
                    url=data["url"],
                    events=data.get("events"),
                    secret=data.get("secret"),
                )
                return jsonify({"status": "registered", "name": data["name"]})
            except Exception as e:
                logger.error(f"Error registering webhook: {e}")
                return jsonify({"error": str(e)}), 500

        @self.app.route("/api/webhooks/<name>", methods=["DELETE"])
        def unregister_webhook(name):
            """Delete specified webhook"""
            if self.monitor.event_manager.unregister_webhook(name):
                return jsonify({"status": "removed", "name": name})
            return jsonify({"error": "Webhook not found"}), 404

    def _grab_snapshot_from_source(self):
        """One-shot grab a frame from the first configured camera source.

        Used by /api/snapshot when the monitor isn't running yet, so the zone
        labeler has a background image before Start is pressed. Opening the
        MJPEG feed as an extra reader is fine (it's multi-client).
        """
        try:
            import cv2
            from ..utils.config import CAMERAS
            if not CAMERAS:
                return None
            source = next(iter(CAMERAS.values())).get("source")
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                cap.release()
                return None
            frame = None
            for _ in range(10):
                ok, f = cap.read()
                if ok and f is not None:
                    frame = f
                    break
            cap.release()
            if frame is None:
                return None
            ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            return buf.tobytes() if ok else None
        except Exception as e:
            logger.debug(f"_grab_snapshot_from_source failed: {e}")
            return None

    def run(self):
        """Start API service"""
        logger.info(f"Starting RoomMonitor API server on {self.host}:{self.port}")
        self.app.run(host=self.host, port=self.port, debug=self.debug)

    def run_threaded(self):
        """Start API service in a thread"""
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        logger.info(f"API server started in background thread")
        return thread
