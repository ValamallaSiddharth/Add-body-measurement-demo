import cv2
import numpy as np
from .errors import MeasurementError


def largest_component(mask):
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if count <= 1:
        raise MeasurementError("Body segmentation failed. Use a plain contrasting background.")
    return labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))


def pose_height_bounds(points, visibility, shape):
    """Pose fallback infers crown height; callers must lower confidence."""
    height = shape[0]
    if visibility[0] < 0.6 or max(visibility[29:33]) < 0.6:
        raise MeasurementError("Head or feet not visible enough for a height estimate.")
    shoulder_y, nose_y = points[[11, 12], 1].mean(), points[0, 1]
    if shoulder_y <= nose_y:
        raise MeasurementError("Stand straight with your head upright.")
    top = nose_y - 0.65*(shoulder_y-nose_y)
    bottom = max(points[i, 1] for i in (29, 30, 31, 32) if visibility[i] >= 0.5)
    if top < 0.005*height or bottom > 0.995*height:
        raise MeasurementError("Full body not visible. Leave space above the head and below the feet.")
    return float(top), float(bottom)


def body_mask(bgr, probability, points, visibility):
    if probability is not None:
        try:
            mask = largest_component(np.squeeze(probability) > 0.5)
            if np.count_nonzero(mask) >= 500:
                return mask, False
        except MeasurementError:
            pass
    # Pose-seeded GrabCut fallback; never infer waist size from skeleton alone.
    h, w = bgr.shape[:2]
    top, bottom = pose_height_bounds(points, visibility, bgr.shape)
    visible = points[visibility >= 0.5]
    x0, x1 = max(1, int(visible[:, 0].min()-0.06*w)), min(w-2, int(visible[:, 0].max()+0.06*w))
    y0, y1 = max(1, int(top-0.02*h)), min(h-2, int(bottom+0.01*h))
    if x1 <= x0 or y1 <= y0:
        raise MeasurementError("Body segmentation failed. Retake against a plain background.")
    grab = np.zeros((h, w), np.uint8)
    try:
        cv2.grabCut(bgr, grab, (x0, y0, x1-x0, y1-y0), np.zeros((1, 65)),
                    np.zeros((1, 65)), 3, cv2.GC_INIT_WITH_RECT)
        mask = largest_component((grab == cv2.GC_FGD) | (grab == cv2.GC_PR_FGD))
    except (cv2.error, MeasurementError) as exc:
        raise MeasurementError("Body segmentation failed. Retake against a plain contrasting background.") from exc
    if np.count_nonzero(mask) < 500:
        raise MeasurementError("Body segmentation failed. No reliable outline was found.")
    return mask, True


def torso_width(mask, y_center, x_center, window):
    widths = []
    for y in range(max(0, y_center-window), min(mask.shape[0], y_center+window+1)):
        # Signed differences preserve falling edges (-1), unlike uint8.
        edges = np.diff(np.pad(mask[y].astype(np.int16), (1, 1)))
        for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]-1):
            if start <= x_center <= end:
                widths.append(float(end-start+1))
                break
    if len(widths) < 3:
        raise MeasurementError("Waist outline not found. Keep arms away from the torso and show your waist.")
    median = float(np.median(widths))
    if np.std(widths)/median > 0.15:
        raise MeasurementError("Waist outline is inconsistent. Retake with arms clear of your waist.")
    return median
