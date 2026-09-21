from dataclasses import dataclass
import io
import math
import warnings
import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from .aruco_service import detect_scale
from .errors import MeasurementError
from .pose_service import detect_pose
from .segmentation_service import body_mask, pose_height_bounds, torso_width

Image.MAX_IMAGE_PIXELS = 25_000_000


def decode_image(data):
    if not data:
        raise MeasurementError("Image file is empty.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"}:
                    raise MeasurementError("Unsupported image format. Use JPEG, PNG or WebP; convert HEIC to JPEG first.")
                if min(source.size) < 480:
                    raise MeasurementError("Image resolution is too low. Use at least 480 pixels on the shorter side.")
                if source.width*source.height > Image.MAX_IMAGE_PIXELS:
                    raise MeasurementError("Image is too large. Use at most 25 megapixels.")
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
    except MeasurementError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MeasurementError("Invalid or corrupt image. Upload a readable JPEG, PNG or WebP photo.") from exc


@dataclass
class ViewResult:
    height_cm: float
    shoulder_cm: float
    waist_axis_cm: float
    confidence: float
    fallback: bool
    diagnostics: dict


def validate_pose(points, visibility, shape, front):
    h, w = shape[:2]
    def visible(i):
        return visibility[i] >= 0.5 and 0 < points[i, 0] < w and 0 < points[i, 1] < h
    for name, pair in (("Shoulders", (11, 12)), ("Hips", (23, 24))):
        if not (all(visible(i) for i in pair) if front else any(visible(i) for i in pair)):
            raise MeasurementError(f"{name} could not be detected clearly. Stand straight and keep the torso visible.")
    if not any(visible(i) for i in range(11)):
        raise MeasurementError("Head not visible. Include the whole head in the photo.")
    if not any(visible(i) for i in (29, 30, 31, 32)):
        raise MeasurementError("Feet not visible. Include the whole body.")
    straight_leg = False
    for hip, knee, ankle in ((23, 25, 27), (24, 26, 28)):
        if all(visible(i) for i in (hip, knee, ankle)):
            a, b = points[hip]-points[knee], points[ankle]-points[knee]
            cosine = np.dot(a, b)/max(np.linalg.norm(a)*np.linalg.norm(b), 1e-6)
            if cosine < -0.85 and points[hip, 1] < points[knee, 1] < points[ankle, 1]:
                straight_leg = True
    if not straight_leg:
        raise MeasurementError("Stand upright with straight legs. Seated or cropped photos cannot be measured.")
    shoulder_mid, hip_mid = points[[11, 12]].mean(axis=0), points[[23, 24]].mean(axis=0)
    if hip_mid[1]-shoulder_mid[1] < 0.1*h:
        raise MeasurementError("Torso pose is unclear. Stand straight facing the required direction.")
    if front and np.linalg.norm(points[11]-points[12]) < 0.35*(hip_mid[1]-shoulder_mid[1]):
        raise MeasurementError("Front photo must face the camera, not sideways.")


def analyze_view(bgr, front=True):
    scale, marker_quality = detect_scale(bgr)
    landmarks, probability = detect_pose(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    h, w = bgr.shape[:2]
    points = np.array([[lm.x*w, lm.y*h] for lm in landmarks], dtype=float)
    visibility = np.array([float(lm.visibility or 0) for lm in landmarks])
    if len(points) != 33 or not np.isfinite(points).all():
        raise MeasurementError("Pose landmarks are incomplete. Retake the photo.")
    validate_pose(points, visibility, bgr.shape, front)
    mask, fallback = body_mask(bgr, probability, points, visibility)
    ys, xs = np.where(mask)
    if len(ys) < 500 or np.ptp(ys) < 0.55*h:
        raise MeasurementError("Full body is too small in frame or segmentation failed. Move closer while keeping head and feet visible.")
    if ys.min() < 0.005*h or ys.max() > 0.995*h or xs.min() < 0.005*w or xs.max() > 0.995*w:
        raise MeasurementError("Full body not visible. Leave a margin around your head, feet and arms.")
    if fallback:
        top, bottom = pose_height_bounds(points, visibility, bgr.shape)
    else:
        top, bottom = float(ys.min()), float(ys.max())
        feet = [points[i, 1] for i in (29, 30, 31, 32) if visibility[i] >= 0.5]
        face = [points[i, 1] for i in range(11) if visibility[i] >= 0.5]
        if top >= min(face) or bottom < max(feet)-0.025*h:
            top, bottom = pose_height_bounds(points, visibility, bgr.shape)
            fallback = True
    shoulder_mid, hip_mid = points[[11, 12]].mean(axis=0), points[[23, 24]].mean(axis=0)
    waist_point = shoulder_mid + 0.60*(hip_mid-shoulder_mid)
    waist_px = torso_width(mask, int(round(waist_point[1])), int(round(waist_point[0])), max(3, int(0.008*h)))
    if front:
        pose_quality = float(np.mean(visibility[[0, 11, 12, 23, 24, 27, 28]]))
    else:
        pose_quality = float(np.mean([max(visibility[list(pair)]) for pair in ((0, 7, 8), (11, 12), (23, 24), (27, 28))]))
    framing_quality = float(np.clip(min(top/h, (h-1-bottom)/h)/0.025, 0, 1))
    quality = 0.30*marker_quality + 0.45*pose_quality + 0.15*(0.4 if fallback else 1) + 0.10*framing_quality
    return ViewResult((bottom-top)/scale, float(np.linalg.norm(points[11]-points[12]))/scale,
                      waist_px/scale, float(quality), fallback,
                      {"pixels_per_cm": round(scale, 4), "marker_quality": round(marker_quality, 3),
                       "pose_visibility": round(pose_quality, 3), "height_method": "pose_fallback" if fallback else "segmentation",
                       "waist_y_px": int(waist_point[1])})


def ellipse_circumference(width_cm, depth_cm):
    a, b = width_cm/2, depth_cm/2
    if not all(math.isfinite(v) and v > 0 for v in (a, b)):
        raise MeasurementError("Waist dimensions are invalid. Retake both photos.")
    h = ((a-b)/(a+b))**2
    return math.pi*(a+b)*(1 + 3*h/(10 + math.sqrt(4-3*h)))


def measure_person(front_bgr, side_bgr):
    views = []
    for name, photo, is_front in (("Front", front_bgr, True), ("Side", side_bgr, False)):
        try:
            views.append(analyze_view(photo, is_front))
        except MeasurementError as exc:
            raise MeasurementError(f"{name} photo: {exc}") from exc
    front, side = views
    if side.shoulder_cm > front.shoulder_cm*0.80:
        raise MeasurementError("Side photo appears too front-facing. Turn fully sideways and retake it.")
    disagreement = abs(front.height_cm-side.height_cm)
    if disagreement > max(8, 0.06*front.height_cm):
        raise MeasurementError("Front and side scales disagree. Keep the 15 cm marker beside your body at the same depth in each photo.")
    waist = ellipse_circumference(front.waist_axis_cm, side.waist_axis_cm)
    if not (50 <= front.height_cm <= 250 and 10 <= front.shoulder_cm <= 85 and 30 <= waist <= 230):
        raise MeasurementError("Measurements are outside the supported range. Check the printed marker is exactly 15 cm and the full body is visible.")
    confidence = min(front.confidence, side.confidence)*max(0.65, 1-max(0, disagreement-2)/20)
    if front.fallback or side.fallback:
        confidence = min(confidence, 0.79)
    confidence = round(float(np.clip(confidence, 0, 1)), 3)
    if confidence < 0.65:
        raise MeasurementError(f"Retake recommended: capture confidence {confidence:.0%}. Use clearer photos, a square-facing marker and a plain background. Nothing was saved.")
    notes = ["Waist circumference is an ellipse estimate at a pose-derived waist level. Confidence indicates capture quality, not measured accuracy."]
    if front.fallback or side.fallback:
        notes.append("Pose-based height/segmentation fallback was used; verify manually.")
    if disagreement > 3:
        notes.append(f"Front/side height differed by {disagreement:.1f} cm; check marker placement.")
    return {"height_cm": round(front.height_cm, 1), "shoulder_cm": round(front.shoulder_cm, 1),
            "waist_cm": round(waist, 1), "front_waist_width_cm": round(front.waist_axis_cm, 1),
            "side_waist_depth_cm": round(side.waist_axis_cm, 1), "confidence": confidence,
            "status": "good" if confidence >= 0.85 else "review", "notes": " ".join(notes),
            "diagnostics": {"front": front.diagnostics, "side": side.diagnostics}}
