import cv2
import numpy as np
from .errors import MeasurementError

MARKER_CM = 15.0
MARKER_ID = 0


def marker_image(size=600):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    return cv2.aruco.generateImageMarker(dictionary, MARKER_ID, size)


def detect_scale(bgr):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    if ids is None or MARKER_ID not in ids.flatten():
        raise MeasurementError("Calibration marker not detected. Use the supplied DICT_4X4_50, ID 0 marker and retake the photo.")
    matches = np.where(ids.flatten() == MARKER_ID)[0]
    if len(matches) != 1:
        raise MeasurementError("Use only one calibration marker in the photo.")
    points = corners[int(matches[0])][0].astype(float)
    sides = np.linalg.norm(np.roll(points, -1, axis=0) - points, axis=1)
    width = float(sides.mean())
    variation = float(sides.std() / width)
    if width < 45:
        raise MeasurementError("Calibration marker is too small or blurry. Move closer or use a higher-resolution photo.")
    diagonals = [np.linalg.norm(points[0]-points[2]), np.linalg.norm(points[1]-points[3])]
    if variation > 0.15 or min(diagonals)/max(diagonals) < 0.8:
        raise MeasurementError("Marker is tilted too far. Keep it flat and facing the camera.")
    quality = float(np.clip((1-variation/0.2)*min(1, width/75), 0, 1))
    return width/MARKER_CM, quality


def marker_svg():
    bits = marker_image(6)
    squares = ''.join(f'<rect x="{x}" y="{y}" width="1" height="1"/>'
                      for y in range(6) for x in range(6) if bits[y, x] == 0)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="150mm" height="150mm" '
            'viewBox="0 0 6 6" shape-rendering="crispEdges" aria-label="15 centimetre ArUco marker">'
            '<title>DICT_4X4_50 ID 0. Print outer black square at exactly 15 cm by 15 cm.</title>'
            '<rect width="6" height="6" fill="white"/>' + squares + '</svg>')


def marker_page():
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Print 15 cm calibration marker</title><style>
@page{size:A4 portrait;margin:15mm}body{font:14px Arial;margin:0;color:#000;background:white}
h1{font-size:20px}.marker{margin:10mm auto;width:150mm;height:150mm}
button{padding:12px;margin:12px 0}@media print{button,a{display:none}}
</style></head><body><h1>Print this black square at exactly 15 cm × 15 cm</h1>
<p>ArUco DICT_4X4_50 · ID 0. Choose A4 paper, 100% / Actual size.
Disable Fit to page, headers and footers. Keep the white margin around the square.</p>
<button onclick="window.print()">Print marker</button> <a href="/">Back to measurement</a>
<div class="marker">''' + marker_svg() + '''</div>
<p><b>Check with a ruler:</b> the outside black edge must measure exactly 15 cm on each side.
The white margin is not included. Do not use the marker until its printed size is correct.</p>
<p>Mount flat and upright beside the person, facing the camera, at the same distance from
the camera as the person's body. Show it in both photos.</p></body></html>'''
