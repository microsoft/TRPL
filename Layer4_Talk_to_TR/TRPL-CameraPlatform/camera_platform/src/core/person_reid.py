# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Cross-camera person re-identification module (Person ReID)
Uses OSNet to extract appearance features, matches cross-camera persons via cosine similarity.
Tracklet-level embedding aggregation for robust matching.
"""
import cv2
import numpy as np
import torch
import torchvision.transforms as T
import logging
import os
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


class PersonReID:
    """Person re-identification feature extractor with tracklet aggregation"""

    # Quality filter thresholds (relaxed for close-range scenarios)
    MIN_BBOX_HEIGHT = 30       # Minimum crop height in pixels
    MIN_BBOX_WIDTH = 15        # Minimum crop width in pixels
    MIN_BBOX_AREA = 800        # Minimum crop area
    MAX_ASPECT_RATIO = 6.0     # Max height/width ratio
    MIN_ASPECT_RATIO = 0.2     # Min height/width ratio (allow wide partial crops)
    MIN_KPT_CONF = 0.15        # Mean keypoint confidence threshold
    MIN_QUALITY_SCORE = 0.15   # Below this, don't use the embedding at all

    # Tracklet buffer config
    TRACKLET_BUFFER_SIZE = 20  # Max embeddings per tracklet
    MIN_TRACKLET_SAMPLES = 3   # Need this many samples before aggregated matching

    def __init__(self, model_name: str = "osnet_x0_25", match_threshold: float = 0.6):
        """
        Args:
            model_name: torchreid model name (osnet_x0_25 lightweight, osnet_x1_0 more accurate)
            match_threshold: Cosine similarity match threshold, higher = stricter
        """
        self.match_threshold = match_threshold
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self._load_model(model_name)
        self.transform = T.Compose([
            T.ToPILImage(),
            T.Resize((256, 128)),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        # Feature gallery for each global person_id
        # Stores (feature, quality_score) pairs; quality-weighted aggregation for matching
        self._feature_gallery: Dict[int, List[Tuple[np.ndarray, float]]] = {}
        self._max_gallery_size = 10

        # Outlier rejection: drop features with cosine sim < this vs gallery mean
        self._gallery_outlier_threshold = 0.5

        # Tracklet embedding buffers: (camera_id, local_track_id) -> [(embedding, quality_score), ...]
        self._tracklet_buffers: Dict[Tuple[str, int], List[Tuple[np.ndarray, float]]] = {}

    def _load_model(self, model_name: str):
        """Load ReID model"""
        if os.getenv("REID_ENABLED", "true").strip().lower() in {
            "0", "false", "no", "off"
        }:
            logger.warning(
                "Person ReID disabled; using same-camera acceptance features only"
            )
            return torch.nn.Sequential(
                torch.nn.AdaptiveAvgPool2d((1, 1)),
                torch.nn.Flatten(),
            ).to(self.device)
        try:
            import torchreid
            model = torchreid.models.build_model(
                name=model_name,
                num_classes=1000,
                pretrained=True,
            )
            model = model.to(self.device)
            model.eval()
            logger.info(f"Person ReID model loaded: {model_name} on {self.device}")
            return model
        except Exception as e:
            logger.error(f"Failed to load torchreid model: {e}")
            logger.info("Falling back to torchvision ResNet50")
            return self._load_fallback()

    def _load_fallback(self):
        """Fallback: use torchvision ResNet50 for feature extraction"""
        try:
            import torchvision.models as models
            model = models.resnet50(weights="DEFAULT")
            model.fc = torch.nn.Identity()
            model = model.to(self.device)
            model.eval()
            logger.info("Fallback ReID model loaded: ResNet50")
            return model
        except Exception as e:
            logger.error(f"Failed to load fallback model: {e}")
            raise RuntimeError("No ReID model available") from e

    def extract_feature(self, frame: np.ndarray, bbox: np.ndarray) -> Optional[np.ndarray]:
        """
        Crop person region from frame and extract feature vector

        Args:
            frame: Original frame (BGR)
            bbox: Bounding box [x1, y1, x2, y2]

        Returns:
            L2-normalized feature vector, or None on failure
        """
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0 or (y2 - y1) < 20 or (x2 - x1) < 10:
            return None

        try:
            crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            tensor = self.transform(crop_rgb).unsqueeze(0).to(self.device)

            with torch.no_grad():
                feature = self.model(tensor)

            feature = feature.cpu().numpy().flatten()
            norm = np.linalg.norm(feature)
            if norm < 1e-6:
                return None
            feature = feature / norm
            return feature
        except Exception as e:
            logger.error(f"ReID feature extraction error: {e}")
            return None

    def compute_quality_score(self, bbox: np.ndarray, frame_shape: Tuple[int, int],
                               kpt_conf: Optional[np.ndarray] = None) -> float:
        """
        Compute a quality score for a person crop. Higher = better for ReID.

        Args:
            bbox: [x1, y1, x2, y2]
            frame_shape: (height, width) of the original frame
            kpt_conf: Keypoint confidence array (17 values for COCO), optional

        Returns:
            Quality score in [0, 1]
        """
        x1, y1, x2, y2 = bbox[:4]
        w = x2 - x1
        h = y2 - y1
        area = w * h
        frame_h, frame_w = frame_shape[:2]

        # Hard filters — return 0 immediately
        if h < self.MIN_BBOX_HEIGHT or w < self.MIN_BBOX_WIDTH:
            return 0.0
        if area < self.MIN_BBOX_AREA:
            return 0.0
        aspect = h / max(w, 1)
        if aspect > self.MAX_ASPECT_RATIO or aspect < self.MIN_ASPECT_RATIO:
            return 0.0

        # Size score: larger crop relative to frame = better (capped at 1.0)
        size_ratio = area / (frame_h * frame_w)
        size_score = min(size_ratio * 20, 1.0)  # 5% of frame area → score 1.0

        # Edge penalty: person at frame border is likely truncated
        edge_margin = 5
        at_edge = (x1 < edge_margin or y1 < edge_margin or
                   x2 > frame_w - edge_margin or y2 > frame_h - edge_margin)
        edge_score = 0.6 if at_edge else 1.0

        # Aspect ratio score: ideal person aspect ~2.0-3.0
        aspect_score = 1.0 if 1.5 <= aspect <= 3.5 else 0.7

        # Keypoint confidence score
        kpt_score = 1.0
        if kpt_conf is not None and len(kpt_conf) > 0:
            mean_conf = float(np.mean(kpt_conf))
            if mean_conf < self.MIN_KPT_CONF:
                return 0.0
            kpt_score = min(mean_conf / 0.6, 1.0)  # 0.6 conf → score 1.0

        quality = size_score * edge_score * aspect_score * kpt_score
        return float(np.clip(quality, 0.0, 1.0))

    def add_to_tracklet(self, camera_id: str, local_track_id: int,
                        embedding: np.ndarray, quality_score: float):
        """
        Add an embedding to a tracklet's buffer (quality-filtered).

        Args:
            camera_id: Camera ID
            local_track_id: Local tracker ID
            embedding: L2-normalized feature vector
            quality_score: Quality score from compute_quality_score
        """
        if quality_score < self.MIN_QUALITY_SCORE:
            return

        key = (camera_id, local_track_id)
        if key not in self._tracklet_buffers:
            self._tracklet_buffers[key] = []

        buf = self._tracklet_buffers[key]
        buf.append((embedding, quality_score))

        # Keep buffer bounded, drop lowest-quality sample when full
        if len(buf) > self.TRACKLET_BUFFER_SIZE:
            min_idx = min(range(len(buf)), key=lambda i: buf[i][1])
            buf.pop(min_idx)

    def get_tracklet_feature(self, camera_id: str, local_track_id: int) -> Optional[np.ndarray]:
        """
        Get quality-weighted aggregated feature for a tracklet.

        Returns:
            L2-normalized aggregated feature, or None if insufficient samples
        """
        key = (camera_id, local_track_id)
        buf = self._tracklet_buffers.get(key, [])
        if not buf:
            return None

        embeddings = np.array([e for e, q in buf])
        weights = np.array([q for e, q in buf])

        # Quality-weighted mean
        weighted = np.average(embeddings, axis=0, weights=weights)
        norm = np.linalg.norm(weighted)
        if norm < 1e-6:
            return None
        return weighted / norm

    def tracklet_sample_count(self, camera_id: str, local_track_id: int) -> int:
        """How many embeddings in this tracklet's buffer."""
        return len(self._tracklet_buffers.get((camera_id, local_track_id), []))

    def remove_tracklet(self, camera_id: str, local_track_id: int):
        """Remove a tracklet buffer."""
        self._tracklet_buffers.pop((camera_id, local_track_id), None)

    def update_gallery(self, global_person_id: int, feature: np.ndarray,
                       quality: float = 1.0):
        """
        Update a person's feature gallery with outlier pruning.

        New features are checked against the existing gallery mean. If the
        cosine similarity is below a threshold, the feature is likely from
        an occlusion or misdetection and is discarded.
        """
        if global_person_id not in self._feature_gallery:
            self._feature_gallery[global_person_id] = []

        gallery = self._feature_gallery[global_person_id]

        # Outlier rejection: skip features that diverge too much from gallery mean
        if len(gallery) >= 3:
            mean_feat = self._compute_gallery_mean(gallery)
            if mean_feat is not None:
                sim = float(np.dot(feature, mean_feat))
                if sim < self._gallery_outlier_threshold:
                    return  # Likely occlusion/misdetection — discard

        gallery.append((feature, quality))

        # Evict the lowest-quality sample when over capacity
        if len(gallery) > self._max_gallery_size:
            min_idx = min(range(len(gallery)), key=lambda i: gallery[i][1])
            gallery.pop(min_idx)

    @staticmethod
    def _compute_gallery_mean(gallery: List[Tuple[np.ndarray, float]]) -> Optional[np.ndarray]:
        """Compute quality-weighted mean feature from gallery."""
        if not gallery:
            return None
        features = np.array([f for f, q in gallery])
        weights = np.array([q for f, q in gallery])
        # Avoid zero-weight edge case
        if weights.sum() < 1e-8:
            mean_feat = np.mean(features, axis=0)
        else:
            mean_feat = np.average(features, axis=0, weights=weights)
        norm = np.linalg.norm(mean_feat)
        if norm < 1e-6:
            return None
        return mean_feat / norm

    def get_mean_feature(self, global_person_id: int) -> Optional[np.ndarray]:
        """Get a person's quality-weighted mean feature."""
        gallery = self._feature_gallery.get(global_person_id, [])
        return self._compute_gallery_mean(gallery)

    def find_match(self, feature: np.ndarray, exclude_ids: set = None) -> Tuple[Optional[int], float]:
        """
        Find the best matching person in the feature gallery.

        Uses top-K highest-quality features per person for a more robust
        match that is less affected by outlier embeddings.

        Args:
            feature: Feature vector to match
            exclude_ids: Set of global_person_ids to exclude

        Returns:
            (matched_global_id, similarity) or (None, 0.0)
        """
        best_id = None
        best_sim = 0.0
        exclude_ids = exclude_ids or set()
        top_k = 5  # Use top-K quality features for matching

        for gid, gallery in self._feature_gallery.items():
            if gid in exclude_ids:
                continue
            if not gallery:
                continue

            # Sort by quality descending, take top-K
            sorted_gallery = sorted(gallery, key=lambda x: x[1], reverse=True)[:top_k]
            mean_feat = self._compute_gallery_mean(sorted_gallery)
            if mean_feat is None:
                continue
            sim = float(np.dot(feature, mean_feat))

            if sim > best_sim:
                best_sim = sim
                best_id = gid

        if best_sim >= self.match_threshold:
            return best_id, best_sim
        return None, best_sim

    def remove_person(self, global_person_id: int):
        """Remove a person's feature gallery"""
        self._feature_gallery.pop(global_person_id, None)
