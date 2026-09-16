# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Snapshot Manager

Provides access to the per-camera frame ring buffers maintained by CameraSource.
Orchestrator calls this to obtain frames before dispatching to VLMObserverWorker.
"""
import logging
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class SnapshotManager:
    """
    Thin façade over the camera ring buffers.

    `ring_buffers` is a dict {camera_id: deque[(timestamp, frame)]}.
    Pass in CameraSource.frame_ring_buffer references after cameras are set up.
    """

    def __init__(self):
        # camera_id → deque of (timestamp: float, frame: np.ndarray)
        self._buffers: Dict[str, deque] = {}
        # camera_id → deque of (timestamp: float, annotated_frame: np.ndarray)
        # Stores YOLO-annotated frames (with bboxes drawn). Used for VLM snapshots
        # so the model can see which persons/groups were detected.
        self._annotated_buffers: Dict[str, deque] = {}

    def register_camera(self, camera_id: str, ring_buffer: deque) -> None:
        self._buffers[camera_id] = ring_buffer

    def register_annotated_camera(self, camera_id: str, annotated_buffer: deque) -> None:
        """Register the annotated (YOLO-drawn) frame buffer for a camera."""
        self._annotated_buffers[camera_id] = annotated_buffer

    # ------------------------------------------------------------------
    # Single-frame snapshot
    # ------------------------------------------------------------------

    def get_latest_frame(self, camera_id: Optional[str] = None) -> Optional[np.ndarray]:
        """
        Return the most recent frame from a camera (or any camera if camera_id is None).
        """
        if camera_id is not None:
            buf = self._buffers.get(camera_id)
            if buf and len(buf) > 0:
                _, frame = buf[-1]
                return frame
            return None

        # Pick the camera with the most-recent frame
        best_frame = None
        best_ts = 0.0
        for buf in self._buffers.values():
            if buf and len(buf) > 0:
                ts, frame = buf[-1]
                if ts > best_ts:
                    best_ts = ts
                    best_frame = frame
        return best_frame

    # ------------------------------------------------------------------
    # Multi-frame snapshot (for VLM)
    # ------------------------------------------------------------------

    def get_multi_frame(self,
                        n: int = 3,
                        interval_sec: float = 2.0,
                        camera_id: Optional[str] = None) -> List[np.ndarray]:
        """
        Return up to `n` frames spaced approximately `interval_sec` apart,
        taken from the ring buffer going backwards in time.

        Args:
            n:            Number of frames to collect.
            interval_sec: Approximate temporal gap between sampled frames.
            camera_id:    Specific camera to sample; uses best-available if None.

        Returns:
            List of frames, oldest first, length <= n.
        """
        buf = self._select_buffer(camera_id)
        if buf is None or len(buf) == 0:
            return []

        # Convert deque to list for indexed access
        frames = list(buf)  # [(ts, frame), ...]
        if not frames:
            return []

        now = frames[-1][0]
        result: List[np.ndarray] = []
        next_target = now

        # Walk backwards through time collecting samples
        for ts, frame in reversed(frames):
            if ts <= next_target + 0.2:   # allow ±0.2s tolerance
                result.append(frame)
                next_target = ts - interval_sec
                if len(result) >= n:
                    break

        result.reverse()  # Return oldest → newest
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _select_buffer(self, camera_id: Optional[str], prefer_annotated: bool = True) -> Optional[deque]:
        """Select a frame buffer, preferring the annotated buffer if available."""
        if camera_id is not None:
            if prefer_annotated and camera_id in self._annotated_buffers:
                return self._annotated_buffers[camera_id]
            return self._buffers.get(camera_id)
        # Choose the camera whose annotated buffer has the most recent frame
        source = self._annotated_buffers if (prefer_annotated and self._annotated_buffers) else self._buffers
        best_buf = None
        best_ts = 0.0
        for buf in source.values():
            if buf and len(buf) > 0:
                ts, _ = buf[-1]
                if ts > best_ts:
                    best_ts = ts
                    best_buf = buf
        return best_buf
