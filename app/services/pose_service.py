from pathlib import Path
from threading import Lock
import mediapipe as mp
import numpy as np
from ..config import settings
from .errors import MeasurementError

_detector = None
_lock = Lock()


def initialize_pose():
    global _detector
    with _lock:
        if _detector is None:
            if not Path(settings.pose_model_path).is_file():
                raise RuntimeError("Pose model missing. Rebuild Docker to download it.")
            options = mp.tasks.vision.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=settings.pose_model_path),
                running_mode=mp.tasks.vision.RunningMode.IMAGE, num_poses=2,
                min_pose_detection_confidence=0.5, min_pose_presence_confidence=0.5,
                output_segmentation_masks=True)
            _detector = mp.tasks.vision.PoseLandmarker.create_from_options(options)


def close_pose():
    global _detector
    with _lock:
        if _detector is not None:
            _detector.close()
            _detector = None


def detect_pose(rgb):
    initialize_pose()
    try:
        with _lock:
            result = _detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
            if not result.pose_landmarks:
                raise MeasurementError("No person detected. Use a clear, standing full-body photo.")
            if len(result.pose_landmarks) != 1:
                raise MeasurementError("More than one person detected. Only the person being measured should be in frame.")
            mask = result.segmentation_masks[0].numpy_view().copy() if result.segmentation_masks else None
            return result.pose_landmarks[0], mask
    except MeasurementError:
        raise
    except Exception as exc:
        raise MeasurementError("Pose detection failed. Retake a sharp photo with the whole body visible.") from exc
