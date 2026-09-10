import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.room_monitor import RoomMonitor


def _monitor_without_runtime_dependencies() -> RoomMonitor:
    monitor = RoomMonitor.__new__(RoomMonitor)
    monitor._monitor_thread = None
    monitor._camera_setup_thread = None
    monitor._monitor_start_event = threading.Event()
    monitor._monitor_stop_event = threading.Event()
    monitor._monitor_start_error = None
    monitor._stop_capture = Mock()
    return monitor


def test_start_async_reports_unavailable_camera_source():
    monitor = _monitor_without_runtime_dependencies()
    monitor.setup_cameras = Mock(return_value=False)

    with patch.dict("os.environ", {"CAMERA_START_TIMEOUT_SECONDS": "1"}):
        assert monitor.start_async() is False

    assert monitor.is_running is False
    assert "No camera source could be opened" in monitor.start_error
    monitor._stop_capture.assert_called_once()


def test_start_async_reports_setup_exception():
    monitor = _monitor_without_runtime_dependencies()
    monitor.setup_cameras = Mock(side_effect=RuntimeError("capture exploded"))

    with patch.dict("os.environ", {"CAMERA_START_TIMEOUT_SECONDS": "1"}):
        assert monitor.start_async() is False

    assert monitor.is_running is False
    assert monitor.start_error == "Camera setup failed: capture exploded"
    monitor._stop_capture.assert_called_once()


def test_start_async_cancels_setup_after_timeout():
    monitor = _monitor_without_runtime_dependencies()

    setup_release = threading.Event()

    def delayed_setup(cancel_event=None):
        setup_release.wait(timeout=1)
        return True

    monitor.setup_cameras = delayed_setup

    with patch.dict("os.environ", {"CAMERA_START_TIMEOUT_SECONDS": "0.01"}):
        assert monitor.start_async() is False

    assert monitor.is_running is False
    assert monitor._monitor_stop_event.is_set()
    assert "did not complete" in monitor.start_error
    assert monitor.start_async() is False
    assert "still finishing" in monitor.start_error

    setup_release.set()
    monitor._camera_setup_thread.join(timeout=1)
    assert monitor._stop_capture.call_count >= 1
