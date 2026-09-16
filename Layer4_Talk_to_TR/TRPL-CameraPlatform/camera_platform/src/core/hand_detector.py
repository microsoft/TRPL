# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Hand raise detection module (professional version)
Based on body-local coordinate system + normalized ratios + multi-condition scoring + temporal stability
"""
import numpy as np
import cv2
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple


class HandDetector:
    """
    Professional hand raise detector (adapted for COCO 17 keypoints)
    Core features:
    1) Uses body-local coordinate system, independent of raw image y-axis
    2) Uses normalized ratios, independent of fixed pixel thresholds
    3) Checks raised wrist, arm direction, elbow form, cross-body false positives
    4) Built-in temporal stability (multi-frame confirmation per track_id)
    """

    KEYPOINT_NAMES = {
        0: "nose",
        1: "left_eye", 2: "right_eye", 3: "left_ear", 4: "right_ear",
        5: "left_shoulder", 6: "right_shoulder",
        7: "left_elbow", 8: "right_elbow",
        9: "left_wrist", 10: "right_wrist",
        11: "left_hip", 12: "right_hip",
        13: "left_knee", 14: "right_knee",
        15: "left_ankle", 16: "right_ankle",
    }

    SIDE_IDXS = {
        "left":  {"shoulder": 5, "elbow": 7, "wrist": 9,  "hip": 11},
        "right": {"shoulder": 6, "elbow": 8, "wrist": 10, "hip": 12},
    }

    HEAD_IDXS = [0, 1, 2, 3, 4]

    def __init__(
        self,
        conf_threshold: float = 0.25,
        min_raise_ratio: float = 0.18,
        min_vertical_cos: float = 0.45,
        max_lateral_ratio: float = 0.80,
        min_forearm_up_cos: float = 0.25,
        min_upperarm_up_cos: float = -0.05,
        elbow_angle_threshold: float = 110.0,
        head_margin_ratio: float = 0.02,
        cross_body_allow_ratio: float = 0.20,
        score_threshold: float = 0.48,
        temporal_window: int = 5,
        enter_frames: int = 3,
        exit_frames: int = 2,
        # --- EMA smoothing ---
        score_ema_alpha: float = 0.35,
        # --- Asymmetric hysteresis: exit threshold lower than enter ---
        exit_score_ratio: float = 0.75,
    ):
        self.conf_threshold = conf_threshold
        self.min_raise_ratio = min_raise_ratio
        self.min_vertical_cos = min_vertical_cos
        self.max_lateral_ratio = max_lateral_ratio
        self.min_forearm_up_cos = min_forearm_up_cos
        self.min_upperarm_up_cos = min_upperarm_up_cos
        self.elbow_angle_threshold = elbow_angle_threshold
        self.head_margin_ratio = head_margin_ratio
        self.cross_body_allow_ratio = cross_body_allow_ratio
        self.score_threshold = score_threshold
        self.exit_score_threshold = score_threshold * exit_score_ratio

        self.temporal_window = temporal_window
        self.enter_frames = enter_frames
        self.exit_frames = exit_frames

        self.score_ema_alpha = score_ema_alpha

        self._track_states: Dict[int, Dict[str, object]] = defaultdict(
            lambda: {
                "left_hist": deque(maxlen=self.temporal_window),
                "right_hist": deque(maxlen=self.temporal_window),
                "left_state": False,
                "right_state": False,
                "left_ema": 0.0,
                "right_ema": 0.0,
            }
        )

    # ---- Utility functions ----

    @staticmethod
    def _clip01(v: float) -> float:
        return float(max(0.0, min(1.0, v)))

    @staticmethod
    def _unit(v: np.ndarray, eps: float = 1e-6) -> Tuple[np.ndarray, float]:
        n = float(np.linalg.norm(v))
        if n < eps:
            return np.zeros_like(v, dtype=np.float32), 0.0
        return (v / n).astype(np.float32), n

    @staticmethod
    def angle_degrees(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
        """Calculate angle ABC"""
        a = np.asarray(a, dtype=np.float32)
        b = np.asarray(b, dtype=np.float32)
        c = np.asarray(c, dtype=np.float32)

        ba = a - b
        bc = c - b

        ba_u, ba_n = HandDetector._unit(ba)
        bc_u, bc_n = HandDetector._unit(bc)

        if ba_n == 0.0 or bc_n == 0.0:
            return 0.0

        cosv = float(np.dot(ba_u, bc_u))
        cosv = max(-1.0, min(1.0, cosv))
        return float(np.degrees(np.arccos(cosv)))

    def _is_visible(self, kpts_conf: np.ndarray, idx: int) -> bool:
        return idx < len(kpts_conf) and float(kpts_conf[idx]) >= self.conf_threshold

    def _mean_visible(self, kpts_xy: np.ndarray, kpts_conf: np.ndarray, indices) -> Optional[np.ndarray]:
        pts = [kpts_xy[i] for i in indices if self._is_visible(kpts_conf, i)]
        if not pts:
            return None
        return np.mean(np.asarray(pts, dtype=np.float32), axis=0)

    # ---- Body-local coordinate system ----

    def _body_frame(self, kpts_xy: np.ndarray, kpts_conf: np.ndarray, side: str) -> Dict[str, object]:
        """
        Build body-local coordinate system:
        - up: Body upward direction
        - right: Body rightward direction
        - scale: Normalization scale (prefers torso)
        """
        ids = self.SIDE_IDXS[side]
        shoulder_idx = ids["shoulder"]
        hip_idx = ids["hip"]

        shoulder = kpts_xy[shoulder_idx].astype(np.float32)
        shoulder_center = self._mean_visible(kpts_xy, kpts_conf, [5, 6])
        hip_center = self._mean_visible(kpts_xy, kpts_conf, [11, 12])

        if self._is_visible(kpts_conf, shoulder_idx) and self._is_visible(kpts_conf, hip_idx):
            up_vec = shoulder - kpts_xy[hip_idx].astype(np.float32)
        elif shoulder_center is not None and hip_center is not None:
            up_vec = shoulder_center - hip_center
        else:
            up_vec = np.array([0.0, -1.0], dtype=np.float32)

        up, up_norm = self._unit(up_vec)
        if up_norm == 0.0:
            up = np.array([0.0, -1.0], dtype=np.float32)

        if self._is_visible(kpts_conf, 5) and self._is_visible(kpts_conf, 6):
            right_vec = kpts_xy[6].astype(np.float32) - kpts_xy[5].astype(np.float32)
            shoulder_width = float(np.linalg.norm(right_vec))
        elif self._is_visible(kpts_conf, 11) and self._is_visible(kpts_conf, 12):
            right_vec = kpts_xy[12].astype(np.float32) - kpts_xy[11].astype(np.float32)
            shoulder_width = float(np.linalg.norm(right_vec))
        else:
            right_vec = np.array([up[1], -up[0]], dtype=np.float32)
            shoulder_width = 0.0

        right, right_norm = self._unit(right_vec)
        if right_norm == 0.0:
            right = np.array([1.0, 0.0], dtype=np.float32)

        if self._is_visible(kpts_conf, shoulder_idx) and self._is_visible(kpts_conf, hip_idx):
            scale = float(np.linalg.norm(kpts_xy[shoulder_idx] - kpts_xy[hip_idx]))
        elif shoulder_center is not None and hip_center is not None:
            scale = float(np.linalg.norm(shoulder_center - hip_center))
        else:
            scale = max(shoulder_width * 1.5, 1.0)

        return {
            "up": up,
            "right": right,
            "scale": max(scale, 1.0),
            "shoulder_center": shoulder_center if shoulder_center is not None else shoulder,
            "shoulder_width": max(shoulder_width, 1.0),
        }

    def _head_top_point(
        self, kpts_xy: np.ndarray, kpts_conf: np.ndarray,
        origin: np.ndarray, up: np.ndarray,
    ) -> Optional[np.ndarray]:
        head_pts = [kpts_xy[i].astype(np.float32) for i in self.HEAD_IDXS if self._is_visible(kpts_conf, i)]
        if not head_pts:
            return None
        proj = [float(np.dot(p - origin, up)) for p in head_pts]
        best_idx = int(np.argmax(proj))
        return head_pts[best_idx]

    # ---- Single-side scoring ----

    def _score_side(self, kpts_xy: np.ndarray, kpts_conf: np.ndarray, side: str) -> Dict[str, object]:
        """
        Comprehensive single-side hand raise determination:
        - Essential conditions: raised + roughly upward + no cross-body + not obvious lateral raise
        - Supporting evidence: head relationship / elbow angle / upper/forearm direction
        - Outputs score and reason for debugging
        """
        ids = self.SIDE_IDXS[side]
        s_idx, e_idx, w_idx = ids["shoulder"], ids["elbow"], ids["wrist"]

        if not self._is_visible(kpts_conf, s_idx) or not self._is_visible(kpts_conf, w_idx):
            return {
                "raised": False, "valid": False, "score": 0.0,
                "reason": "shoulder/wrist confidence too low", "metrics": {},
            }

        shoulder = kpts_xy[s_idx].astype(np.float32)
        wrist = kpts_xy[w_idx].astype(np.float32)

        frame = self._body_frame(kpts_xy, kpts_conf, side)
        up = frame["up"]
        right = frame["right"]
        scale = float(frame["scale"])
        shoulder_center = frame["shoulder_center"].astype(np.float32)
        shoulder_width = float(frame["shoulder_width"])

        sw = wrist - shoulder
        sw_u, sw_len = self._unit(sw)
        if sw_len == 0.0:
            return {
                "raised": False, "valid": False, "score": 0.0,
                "reason": "wrist overlaps shoulder", "metrics": {},
            }

        # 1) Raise height
        raise_ratio = float(np.dot(sw, up) / scale)
        # 2) Overall upward degree
        vertical_cos = float(np.dot(sw_u, up))
        # 3) Lateral extension degree
        lateral_ratio = abs(float(np.dot(sw, right))) / scale
        # 4) Cross-body suppression
        wrist_body_x = float(np.dot(wrist - shoulder_center, right))
        cross_limit = self.cross_body_allow_ratio * shoulder_width
        cross_body = wrist_body_x > cross_limit if side == "left" else wrist_body_x < -cross_limit
        # 5) Head reference
        head_pt = self._head_top_point(kpts_xy, kpts_conf, shoulder_center, up)
        if head_pt is not None:
            head_gap_ratio = float(np.dot(wrist - head_pt, up) / scale)
            wrist_above_head = head_gap_ratio > self.head_margin_ratio
        else:
            head_gap_ratio = float("nan")
            wrist_above_head = False

        # 6) Elbow related
        elbow_visible = self._is_visible(kpts_conf, e_idx)
        elbow_angle = elbow_raise_ratio = forearm_up_cos = upperarm_up_cos = float("nan")
        elbow_open = elbow_not_low = forearm_up = upperarm_up = False

        if elbow_visible:
            elbow = kpts_xy[e_idx].astype(np.float32)
            se_u, se_len = self._unit(elbow - shoulder)
            ew_u, ew_len = self._unit(wrist - elbow)

            elbow_angle = self.angle_degrees(shoulder, elbow, wrist)
            elbow_raise_ratio = float(np.dot(elbow - shoulder, up) / scale)
            forearm_up_cos = float(np.dot(ew_u, up)) if ew_len > 0 else -1.0
            upperarm_up_cos = float(np.dot(se_u, up)) if se_len > 0 else -1.0

            elbow_open = elbow_angle >= self.elbow_angle_threshold
            elbow_not_low = elbow_raise_ratio > -0.05
            forearm_up = forearm_up_cos > self.min_forearm_up_cos
            upperarm_up = upperarm_up_cos > self.min_upperarm_up_cos

        # ---- Essential conditions ----
        essential_ok = (
            raise_ratio >= self.min_raise_ratio
            and vertical_cos >= self.min_vertical_cos
            and lateral_ratio <= self.max_lateral_ratio
            and not cross_body
        )

        # ---- Supporting evidence ----
        evidence_hits = sum([wrist_above_head, elbow_open, elbow_not_low, forearm_up, upperarm_up])
        min_hits = 2 if elbow_visible else 1

        # ---- Continuous score ----
        score = 0.0
        score += 0.35 * self._clip01((raise_ratio - self.min_raise_ratio) / 0.30)
        score += 0.20 * self._clip01((vertical_cos - self.min_vertical_cos) / (1.0 - self.min_vertical_cos + 1e-6))
        score += 0.15 * self._clip01(1.0 - lateral_ratio / (self.max_lateral_ratio + 1e-6))
        if wrist_above_head:
            score += 0.10
        if elbow_visible:
            score += 0.10 * self._clip01((elbow_angle - self.elbow_angle_threshold) / (180.0 - self.elbow_angle_threshold + 1e-6))
            score += 0.05 * self._clip01((forearm_up_cos - self.min_forearm_up_cos) / (1.0 - self.min_forearm_up_cos + 1e-6))
            score += 0.05 * self._clip01((upperarm_up_cos - self.min_upperarm_up_cos) / (1.0 - self.min_upperarm_up_cos + 1e-6))
        if cross_body:
            score -= 0.35

        # Modulate score by keypoint confidence: low-confidence detections → lower score
        key_confs = [float(kpts_conf[s_idx]), float(kpts_conf[w_idx])]
        if elbow_visible:
            key_confs.append(float(kpts_conf[e_idx]))
        mean_conf = sum(key_confs) / len(key_confs)
        # Soft penalty: conf < 0.5 starts reducing score, linearly
        conf_factor = min(1.0, mean_conf / 0.5)
        score *= conf_factor

        score = self._clip01(score)

        raised = bool(essential_ok and evidence_hits >= min_hits and score >= self.score_threshold)

        if not raised:
            if cross_body:
                reason = "cross-body rejection"
            elif raise_ratio < self.min_raise_ratio:
                reason = "wrist not raised enough"
            elif vertical_cos < self.min_vertical_cos:
                reason = "arm not pointing upward"
            elif lateral_ratio > self.max_lateral_ratio:
                reason = "lateral extension too wide"
            elif evidence_hits < min_hits:
                reason = "insufficient elbow/head evidence"
            elif score < self.score_threshold:
                reason = "overall score too low"
            else:
                reason = "conditions not met"
        else:
            reason = "hand raised"

        return {
            "raised": raised,
            "valid": True,
            "score": score,
            "reason": reason,
            "metrics": {
                "raise_ratio": raise_ratio,
                "vertical_cos": vertical_cos,
                "lateral_ratio": lateral_ratio,
                "cross_body": float(cross_body),
                "wrist_above_head": float(wrist_above_head),
                "head_gap_ratio": head_gap_ratio,
                "elbow_visible": float(elbow_visible),
                "elbow_angle": elbow_angle,
                "elbow_raise_ratio": elbow_raise_ratio,
                "forearm_up_cos": forearm_up_cos,
                "upperarm_up_cos": upperarm_up_cos,
                "evidence_hits": float(evidence_hits),
            },
        }

    # ---- Temporal stability ----

    def _update_temporal_state(self, track_id: int, side: str,
                               instant_flag: bool, raw_score: float) -> bool:
        """
        Two-layer temporal stability:
        1) EMA on the continuous score — smooths jitter between frames
        2) Hit-count in sliding window — prevents single-frame triggers
        3) Asymmetric hysteresis — harder to enter than to exit, reducing flicker

        Args:
            track_id: Tracking ID for this person
            side: "left" or "right"
            instant_flag: Whether this frame's score passed the threshold
            raw_score: The continuous score from _score_side (0.0–1.0)
        """
        state = self._track_states[track_id]
        hist_key = f"{side}_hist"
        state_key = f"{side}_state"
        ema_key = f"{side}_ema"

        # Update EMA score
        prev_ema = float(state[ema_key])
        alpha = self.score_ema_alpha
        ema = alpha * raw_score + (1.0 - alpha) * prev_ema
        state[ema_key] = ema

        # Use EMA-smoothed score for the binary decision
        ema_flag = ema >= self.score_threshold

        hist = state[hist_key]
        hist.append(bool(instant_flag or ema_flag))

        current_state = bool(state[state_key])

        if not current_state:
            # Enter: require enough hits AND EMA above enter threshold
            current_state = sum(hist) >= self.enter_frames and ema >= self.score_threshold
        else:
            # Exit: require all recent frames below AND EMA below lower exit threshold
            recent = list(hist)[-self.exit_frames:]
            if len(recent) >= self.exit_frames and not any(recent):
                if ema < self.exit_score_threshold:
                    current_state = False

        state[state_key] = current_state
        return current_state

    # ---- Main interface ----

    def detect_hand_raise(
        self,
        kpts_xy: np.ndarray,
        kpts_conf: np.ndarray,
        track_id: Optional[int] = None,
    ) -> Tuple[bool, bool, dict]:
        """
        Detect whether both hands are raised

        Args:
            kpts_xy: Keypoint coordinates (17, 2)
            kpts_conf: Keypoint confidences (17,)
            track_id: Tracking ID; enables temporal stability when provided

        Returns:
            (left_raised, right_raised, details_dict)
        """
        kpts_xy = np.asarray(kpts_xy, dtype=np.float32)
        kpts_conf = np.asarray(kpts_conf, dtype=np.float32)

        left_res = self._score_side(kpts_xy, kpts_conf, "left")
        right_res = self._score_side(kpts_xy, kpts_conf, "right")

        instant_left = bool(left_res["raised"])
        instant_right = bool(right_res["raised"])

        if track_id is not None:
            final_left = self._update_temporal_state(
                track_id, "left", instant_left, float(left_res["score"]))
            final_right = self._update_temporal_state(
                track_id, "right", instant_right, float(right_res["score"]))
        else:
            final_left = instant_left
            final_right = instant_right

        details = {
            "track_id": track_id,
            "left_raised": final_left,
            "right_raised": final_right,
            "instant_left_raised": instant_left,
            "instant_right_raised": instant_right,
            "left": left_res,
            "right": right_res,
        }
        return final_left, final_right, details

    # ---- Visualization ----

    @staticmethod
    def draw_keypoints(
        frame: np.ndarray,
        kpts_xy: np.ndarray,
        kpts_conf: np.ndarray,
        indices=None,
        color=(0, 255, 255),
        radius: int = 4,
        text_size: float = 0.45,
        conf_threshold: float = 0.05,
    ):
        if indices is None:
            indices = list(range(min(len(kpts_xy), len(kpts_conf))))

        for idx in indices:
            if idx >= len(kpts_xy) or idx >= len(kpts_conf):
                continue
            if float(kpts_conf[idx]) < conf_threshold:
                continue
            x, y = kpts_xy[idx]
            cv2.circle(frame, (int(x), int(y)), radius, color, -1)
            cv2.putText(
                frame, f"{idx}:{float(kpts_conf[idx]):.2f}",
                (int(x) + 5, int(y) - 5),
                cv2.FONT_HERSHEY_SIMPLEX, text_size, color, 1, cv2.LINE_AA,
            )

    @staticmethod
    def draw_skeleton(
        frame: np.ndarray,
        kpts_xy: np.ndarray,
        kpts_conf: np.ndarray,
        conf_threshold: float = 0.3,
        color=(0, 255, 0),
        thickness: int = 2,
    ):
        connections = [
            (0, 1), (0, 2), (1, 3), (2, 4),
            (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
            (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16),
        ]
        for s, e in connections:
            if s >= len(kpts_conf) or e >= len(kpts_conf):
                continue
            if float(kpts_conf[s]) < conf_threshold or float(kpts_conf[e]) < conf_threshold:
                continue
            p1 = tuple(np.asarray(kpts_xy[s], dtype=np.int32))
            p2 = tuple(np.asarray(kpts_xy[e], dtype=np.int32))
            cv2.line(frame, p1, p2, color, thickness, cv2.LINE_AA)

    def clear_track_state(self, track_id: int):
        """Clear temporal state for a specific track_id"""
        self._track_states.pop(track_id, None)
