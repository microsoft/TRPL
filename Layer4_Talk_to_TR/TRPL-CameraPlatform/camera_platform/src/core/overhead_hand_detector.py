"""
Overhead view hand raise detection module
For scenarios where the camera is mounted above looking down

Overhead geometry principles:
- When a person stands, viewed from above, shoulders/hips cluster at body center, head (nose) is to one side
- When arms hang naturally, wrists are close to body center due to perspective foreshortening
- When raising hands, arms enter the camera plane, wrists move away from body center toward the head direction
- Key metrics: arm extension ratio, wrist beyond head, wrist distance from body center
"""
import numpy as np
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

from .hand_detector import HandDetector


class OverheadHandDetector(HandDetector):
    """
    Overhead view hand raise detector (inherits HandDetector)

    Overrides spatial scoring logic, reuses temporal smoothing and visualization.
    """

    def __init__(
        self,
        conf_threshold: float = 0.20,
        min_arm_extension: float = 0.60,
        min_wrist_distance_ratio: float = 1.3,
        head_alignment_min_cos: float = 0.15,
        min_forearm_extension: float = 0.50,
        score_threshold: float = 0.45,
        temporal_window: int = 5,
        enter_frames: int = 3,
        exit_frames: int = 2,
        # Compatibility parameters (ignored)
        **kwargs,
    ):
        """
        Args:
            conf_threshold: Keypoint confidence threshold
            min_arm_extension: Min arm extension ratio (||wrist-shoulder|| / body_scale)
            min_wrist_distance_ratio: Min wrist distance / torso radius ratio
            head_alignment_min_cos: Min cosine between wrist direction and head direction
            min_forearm_extension: Min forearm extension ratio (||wrist-elbow|| / ||elbow-shoulder||)
            score_threshold: Overall score threshold
            temporal_window: Temporal window size
            enter_frames: Hit frames needed to enter hand raise state
            exit_frames: Consecutive miss frames needed to exit hand raise state
        """
        # Initialize parent class temporal state and utility functions
        super().__init__(
            conf_threshold=conf_threshold,
            temporal_window=temporal_window,
            enter_frames=enter_frames,
            exit_frames=exit_frames,
        )

        # Overhead-specific parameters
        self.min_arm_extension = min_arm_extension
        self.min_wrist_distance_ratio = min_wrist_distance_ratio
        self.head_alignment_min_cos = head_alignment_min_cos
        self.min_forearm_extension = min_forearm_extension
        self.score_threshold = score_threshold

    # ---- Overhead body reference frame ----

    def _overhead_frame(
        self, kpts_xy: np.ndarray, kpts_conf: np.ndarray
    ) -> Optional[Dict[str, object]]:
        """
        Build overhead reference frame:
        - body_center: Torso center (mean of shoulders + hips)
        - head_dir: Head direction unit vector (body_center -> nose)
        - torso_radius: Torso radius (mean distance of shoulder/hip points to center)
        - body_scale: Normalization scale (shoulder width or torso diameter)
        """
        # Collect visible torso keypoints
        torso_idxs = [5, 6, 11, 12]  # Left/right shoulder + left/right hip
        visible_torso = [
            kpts_xy[i].astype(np.float32)
            for i in torso_idxs
            if self._is_visible(kpts_conf, i)
        ]

        if len(visible_torso) < 2:
            return None

        body_center = np.mean(visible_torso, axis=0).astype(np.float32)

        # Torso radius
        dists = [float(np.linalg.norm(p - body_center)) for p in visible_torso]
        torso_radius = max(np.mean(dists), 1.0)

        # Head direction (body_center -> nose)
        if self._is_visible(kpts_conf, 0):
            nose = kpts_xy[0].astype(np.float32)
            head_vec = nose - body_center
            head_dir, head_dist = self._unit(head_vec)
            if head_dist < 1.0:
                head_dir = None
                head_dist = 0.0
        else:
            head_dir = None
            head_dist = 0.0
            nose = None

        # Normalization scale: prefer shoulder width
        if self._is_visible(kpts_conf, 5) and self._is_visible(kpts_conf, 6):
            body_scale = float(np.linalg.norm(kpts_xy[5] - kpts_xy[6]))
        else:
            body_scale = torso_radius * 2.0
        body_scale = max(body_scale, 1.0)

        return {
            "body_center": body_center,
            "head_dir": head_dir,
            "head_dist": head_dist,
            "nose": nose,
            "torso_radius": torso_radius,
            "body_scale": body_scale,
        }

    # ---- Overhead single-side scoring ----

    def _score_side(
        self, kpts_xy: np.ndarray, kpts_conf: np.ndarray, side: str
    ) -> Dict[str, object]:
        """
        Single-side hand raise scoring from overhead view

        Key metrics:
        1. arm_extension: Arm extension ratio (||wrist-shoulder|| / body_scale)
        2. wrist_dist_ratio: Wrist distance / torso radius
        3. head_alignment: Cosine similarity between wrist direction and head direction
        4. wrist_beyond_head: Whether wrist extends beyond head position
        5. forearm_extension: Forearm extension ratio
        """
        ids = self.SIDE_IDXS[side]
        s_idx, e_idx, w_idx = ids["shoulder"], ids["elbow"], ids["wrist"]

        if not self._is_visible(kpts_conf, s_idx) or not self._is_visible(kpts_conf, w_idx):
            return {
                "raised": False, "valid": False, "score": 0.0,
                "reason": "shoulder/wrist confidence too low", "metrics": {},
            }

        frame = self._overhead_frame(kpts_xy, kpts_conf)
        if frame is None:
            return {
                "raised": False, "valid": False, "score": 0.0,
                "reason": "insufficient torso keypoints for overhead frame", "metrics": {},
            }

        shoulder = kpts_xy[s_idx].astype(np.float32)
        wrist = kpts_xy[w_idx].astype(np.float32)
        body_center = frame["body_center"]
        head_dir = frame["head_dir"]
        head_dist = frame["head_dist"]
        torso_radius = frame["torso_radius"]
        body_scale = frame["body_scale"]

        # 1) Arm extension ratio: ||wrist - shoulder|| / body_scale
        sw = wrist - shoulder
        sw_len = float(np.linalg.norm(sw))
        arm_extension = sw_len / body_scale

        # 2) Wrist distance ratio: ||wrist - body_center|| / torso_radius
        wrist_dist = float(np.linalg.norm(wrist - body_center))
        wrist_dist_ratio = wrist_dist / torso_radius

        # 3) Head direction alignment
        wrist_from_center = wrist - body_center
        wrist_dir, wrist_dir_len = self._unit(wrist_from_center)

        if head_dir is not None and wrist_dir_len > 0:
            head_alignment = float(np.dot(wrist_dir, head_dir))
        else:
            head_alignment = 0.0

        # 4) Whether wrist extends beyond head
        if head_dir is not None and head_dist > 0:
            wrist_proj = float(np.dot(wrist_from_center, head_dir))
            wrist_beyond_head = wrist_proj > head_dist
        else:
            wrist_beyond_head = False
            wrist_proj = 0.0

        # 5) Forearm extension
        elbow_visible = self._is_visible(kpts_conf, e_idx)
        forearm_extension = float("nan")

        if elbow_visible:
            elbow = kpts_xy[e_idx].astype(np.float32)
            forearm_len = float(np.linalg.norm(wrist - elbow))
            upperarm_len = float(np.linalg.norm(elbow - shoulder))
            if upperarm_len > 1.0:
                forearm_extension = forearm_len / upperarm_len
            else:
                forearm_extension = 0.0

        # ---- Essential conditions ----
        essential_ok = (
            arm_extension >= self.min_arm_extension
            and wrist_dist_ratio >= self.min_wrist_distance_ratio
        )

        # ---- Overall scoring ----
        score = 0.0
        # Arm extension (most important)
        score += 0.35 * self._clip01(
            (arm_extension - self.min_arm_extension) / 0.80
        )
        # Wrist away from body center
        score += 0.25 * self._clip01(
            (wrist_dist_ratio - self.min_wrist_distance_ratio) / 2.0
        )
        # Wrist beyond head
        if wrist_beyond_head:
            score += 0.20
        # Head direction alignment
        if head_dir is not None:
            score += 0.10 * self._clip01(
                (head_alignment - self.head_alignment_min_cos)
                / (1.0 - self.head_alignment_min_cos + 1e-6)
            )
        # Forearm extension
        if elbow_visible and not np.isnan(forearm_extension):
            score += 0.10 * self._clip01(
                (forearm_extension - self.min_forearm_extension)
                / (1.5 - self.min_forearm_extension + 1e-6)
            )

        score = self._clip01(score)

        raised = bool(essential_ok and score >= self.score_threshold)

        if not raised:
            if arm_extension < self.min_arm_extension:
                reason = "arm not extended enough (overhead)"
            elif wrist_dist_ratio < self.min_wrist_distance_ratio:
                reason = "wrist too close to body center"
            elif score < self.score_threshold:
                reason = "overall score too low (overhead)"
            else:
                reason = "conditions not met"
        else:
            reason = "hand raised (overhead)"

        return {
            "raised": raised,
            "valid": True,
            "score": score,
            "reason": reason,
            "metrics": {
                "arm_extension": arm_extension,
                "wrist_dist_ratio": wrist_dist_ratio,
                "head_alignment": head_alignment,
                "wrist_beyond_head": float(wrist_beyond_head),
                "wrist_proj": wrist_proj if head_dir is not None else float("nan"),
                "forearm_extension": forearm_extension,
                "elbow_visible": float(elbow_visible),
                "torso_radius": torso_radius,
                "body_scale": body_scale,
            },
        }
