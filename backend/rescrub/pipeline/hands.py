"""Hand and finger points (RTMW whole-body via rtmlib).

COCO pose stops at the wrist, so each tracked person's box is passed to RTMW,
which returns 133 COCO-WholeBody points; we keep the hand wrist and fingertips.
"""

import logging

import numpy as np

from .people import PersonObs

log = logging.getLogger(__name__)

# COCO-WholeBody: left hand 91..111, right hand 112..132 (21 points each).
_HAND_POINTS = {0: "hand_wrist", 4: "thumb_tip", 8: "index_tip", 12: "middle_tip", 16: "ring_tip", 20: "pinky_tip"}
HAND_PARTS = {
    **{91 + k: f"l_{name}" for k, name in _HAND_POINTS.items()},
    **{112 + k: f"r_{name}" for k, name in _HAND_POINTS.items()},
}
MIN_SCORE = 0.3


class HandEstimator:
    def __init__(self, model: str, input_size: tuple[int, int], device: str):
        from rtmlib import RTMPose

        backend_device = "cuda" if device.startswith("cuda") else "cpu"
        self.model = RTMPose(model, model_input_size=tuple(input_size), backend="onnxruntime", device=backend_device)

    def add_hands(self, frame: np.ndarray, people: list[PersonObs]) -> None:
        if not people:
            return
        boxes = [list(p.box) for p in people]
        keypoints, scores = self.model(frame, bboxes=boxes)
        for p, kps, sc in zip(people, keypoints, scores):
            for idx, name in HAND_PARTS.items():
                s = float(sc[idx])
                if s >= MIN_SCORE:
                    # SimCC scores can exceed 1; clamp so every `conf` shares a 0..1 scale.
                    p.points[name] = (float(kps[idx][0]), float(kps[idx][1]), min(1.0, s))


def load_hand_estimator(settings, device: str) -> HandEstimator | None:
    if not settings.enable_hands:
        return None
    try:
        return HandEstimator(settings.hand_model, settings.hand_input_size, device)
    except Exception as e:  # model download blocked, missing onnxruntime, ...
        log.warning("Hand model unavailable, continuing with wrist points only: %s", e)
        return None
