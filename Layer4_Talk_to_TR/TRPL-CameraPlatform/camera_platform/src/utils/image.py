# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Image encoding utilities — single source of truth for crop/frame encoding.
"""
import base64
import logging
import os
import time
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def encode_crop(frame: np.ndarray, bbox, pad: int = 10,
                jpeg_quality: int = 75, max_side: int = 0) -> Optional[str]:
    """
    Crop a person bbox from frame and return base64-encoded JPEG.

    Args:
        frame: Full frame (H, W, 3).
        bbox:  [x1, y1, x2, y2] bounding box (pixel coords).
        pad:   Pixels to pad around the bbox.
        jpeg_quality: JPEG compression quality (0-100).
        max_side: If > 0, downscale so the crop's longest side <= max_side
                  before encoding. Cuts upload size + VLM image tokens. Never
                  upscales.

    Returns:
        Base64-encoded JPEG string, or None on failure.
    """
    try:
        h, w = frame.shape[:2]
        x1 = max(0, int(bbox[0]) - pad)
        y1 = max(0, int(bbox[1]) - pad)
        x2 = min(w, int(bbox[2]) + pad)
        y2 = min(h, int(bbox[3]) + pad)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        if max_side and max_side > 0:
            ch, cw = crop.shape[:2]
            longest = max(ch, cw)
            if longest > max_side:
                scale = max_side / float(longest)
                crop = cv2.resize(crop, (max(1, int(cw * scale)), max(1, int(ch * scale))),
                                  interpolation=cv2.INTER_AREA)
        _, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception as e:
        logger.warning(f"encode_crop failed: {e}")
        return None


def encode_frame(frame: np.ndarray, jpeg_quality: int = 60) -> Optional[str]:
    """Encode a full frame to base64 JPEG."""
    try:
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception as e:
        logger.warning(f"encode_frame failed: {e}")
        return None


def decode_b64_to_cv2(b64_str: str) -> Optional[np.ndarray]:
    """Decode a base64 JPEG string back to a cv2 image (numpy array)."""
    try:
        raw = base64.b64decode(b64_str)
        arr = np.frombuffer(raw, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.warning(f"decode_b64_to_cv2 failed: {e}")
        return None


def stitch_crops_horizontal(crop_b64_list: List[str], target_height: int = 256) -> Optional[np.ndarray]:
    """
    Decode multiple base64 crops and stitch them side-by-side.

    Each crop is resized to the same height (preserving aspect ratio)
    then concatenated horizontally. Returns a single numpy image.
    """
    images = []
    for b64 in crop_b64_list:
        img = decode_b64_to_cv2(b64)
        if img is None:
            continue
        h, w = img.shape[:2]
        if h == 0:
            continue
        scale = target_height / h
        new_w = max(1, int(w * scale))
        resized = cv2.resize(img, (new_w, target_height))
        images.append(resized)

    if not images:
        return None
    return np.concatenate(images, axis=1)


# Default folder for saving VLM debug images
_VLM_DEBUG_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "vlm_debug")

# Rotation policy: keep at most this many debug images. Override via env var.
# Over 10h of continuous operation these would otherwise fill disk.
_VLM_DEBUG_MAX_FILES = int(os.environ.get("VLM_DEBUG_MAX_FILES", "500"))


def _rotate_vlm_debug_dir(out_dir: str, max_files: int) -> None:
    """Delete oldest .jpg files in out_dir until count ≤ max_files.

    Best-effort: silently skips on any error (permission, race with writer).
    Called before each save so disk usage stays bounded regardless of uptime.
    """
    try:
        entries = [
            os.path.join(out_dir, name)
            for name in os.listdir(out_dir)
            if name.endswith(".jpg")
        ]
        if len(entries) <= max_files:
            return
        # Sort oldest-first by mtime and trim
        entries.sort(key=lambda p: os.path.getmtime(p))
        excess = len(entries) - max_files
        for path in entries[:excess]:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception as e:
        logger.debug(f"VLM debug rotation skipped: {e}")


def save_vlm_debug_image(
    stitched: np.ndarray,
    person_id: int,
    label: str = "",
    output_dir: Optional[str] = None,
    jpeg_quality: int = 90,
) -> Optional[str]:
    """
    Save a stitched VLM input image to disk for debugging.

    Returns the saved file path, or None on failure.
    """
    out = output_dir or _VLM_DEBUG_DIR
    os.makedirs(out, exist_ok=True)
    _rotate_vlm_debug_dir(out, _VLM_DEBUG_MAX_FILES)

    ts = time.strftime("%Y%m%d_%H%M%S")
    suffix = f"_{label}" if label else ""
    filename = f"p{person_id}_{ts}{suffix}.jpg"
    filepath = os.path.join(out, filename)

    try:
        cv2.imwrite(filepath, stitched, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        logger.info(f"VLM debug image saved: {filepath}")
        return filepath
    except Exception as e:
        logger.warning(f"Failed to save VLM debug image: {e}")
        return None


def stitch_and_encode(crop_b64_list: List[str], target_height: int = 256,
                      jpeg_quality: int = 75) -> Optional[str]:
    """Stitch multiple crops and return as a single base64 JPEG."""
    stitched = stitch_crops_horizontal(crop_b64_list, target_height)
    if stitched is None:
        return None
    try:
        _, buf = cv2.imencode(".jpg", stitched, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception as e:
        logger.warning(f"stitch_and_encode failed: {e}")
        return None
