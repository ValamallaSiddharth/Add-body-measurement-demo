"""Single-file RGB-D body measurement research prototype (Python 3.11).

IMPORTANT: this is the backend, upload UI and processing queue, NOT a native
phone depth-capture app. Ordinary JPEGs cannot supply metric scale. It requires
calibrated RGB-D bundles from a compatible capture client. No height input,
printed marker, default adult height or invented scale is used.

Install: pip install fastapi==0.116.1 uvicorn==0.35.0 python-multipart==0.0.20 sqlalchemy==2.0.43 "psycopg[binary]==3.2.9" mediapipe==0.10.21 opencv-contrib-python==4.11.0.86 numpy==1.26.4 protobuf==4.25.5
Download model: python body_measure_handover.py --download-model
Set DATABASE_URL, CAPTURE_TOKEN and ADMIN_TOKEN (different random secrets,
at least 24 characters). Then: python body_measure_handover.py
Test numeric helpers: python body_measure_handover.py --self-test

Each front/side .npz bundle must contain ONLY these non-pickled numeric arrays:
  rgb: H x W x 3 uint8, upright RGB, at least 480 pixels on the short side
  depth_m: H x W floating axial Z-depth in METRES, aligned to rgb
  intrinsics: 3 x 3 calibrated pinhole K for EXACTLY these image dimensions
  up: 3-element unit vector pointing UP in camera coordinates
  valid: H x W boolean mask of depth samples trusted by the capture client
Images must be undistorted and synchronized. Camera coordinates are X right,
Y down, Z forward. Radial distance MUST be converted to axial depth upstream.
Do not fill depth with a constant or fabricate calibration. The capture client
must transform platform-specific coordinates and record true metric depth.

Waist is an ellipse estimate from front width and side depth, NOT a validated
3D circumference. Shoulder breadth is the distance between pose landmarks,
NOT a certified anatomical measurement. Every result requires review.
8,000 is a campaign size, NOT a tested concurrent-user capacity.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import secrets
import sys
import threading
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import DateTime, JSON, LargeBinary, String, Text, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

MAX_BYTES = 20_000_000
MAX_EXPANDED = 50_000_000
VERSION = "rgbd-ellipse-research-1"
log = logging.getLogger("body_measure")
engine = None
Sessions = None


class Base(DeclarativeBase):
    pass


class Scan(Base):
    __tablename__ = "rgbd_scans_v1"  # Does not alter the existing app's tables.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(100), index=True)
    receipt_hash: Mapped[str] = mapped_column(String(64))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True, default="queued")
    front: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    side: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaptureError(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def check_token(value: str | None, name: str):
    expected = os.environ.get(name, "")
    if not expected or not secrets.compare_digest(value or "", expected):
        raise HTTPException(401, "Invalid access code")


def capture_access(x_capture_token: str | None = Header(None)):
    check_token(x_capture_token, "CAPTURE_TOKEN")


def admin_access(x_admin_token: str | None = Header(None)):
    check_token(x_admin_token, "ADMIN_TOKEN")


def load_bundle(data: bytes):
    expected = {"rgb", "depth_m", "intrinsics", "up", "valid"}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (len(entries) != 5 or {x.filename for x in entries} != {x + ".npy" for x in expected}
                    or sum(x.file_size for x in entries) > MAX_EXPANDED):
                raise CaptureError("Invalid scan bundle or expanded data too large")
        with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
            rgb, depth, k, up, valid = (arrays[n] for n in ("rgb", "depth_m", "intrinsics", "up", "valid"))
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("Upload a calibrated .npz scan bundle, not an ordinary photo") from exc
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise CaptureError("RGB must be an H x W x 3 uint8 array")
    h, w = rgb.shape[:2]
    if min(h, w) < 480 or h * w > 2_000_000:
        raise CaptureError("Use at least 480 pixels per side and at most 2 megapixels")
    if depth.shape != (h, w) or depth.dtype.kind != "f" or valid.shape != (h, w) or valid.dtype != np.bool_:
        raise CaptureError("Aligned depth and a boolean validity mask must match RGB dimensions")
    if (k.shape != (3, 3) or k.dtype.kind not in "fi" or not np.isfinite(k).all()
            or k[0, 0] <= 0 or k[1, 1] <= 0 or not np.allclose(k[2], [0, 0, 1])
            or abs(k[0, 1]) > 1e-6 or abs(k[1, 0]) > 1e-6
            or not 0 <= k[0, 2] < w or not 0 <= k[1, 2] < h):
        raise CaptureError("Invalid camera intrinsics")
    if up.shape != (3,) or up.dtype.kind not in "fi" or not np.isfinite(up).all() or not 0.99 < np.linalg.norm(up) < 1.01:
        raise CaptureError("Camera up vector must be calibrated and normalized")
    if np.dot(up, [0, -1, 0]) < math.cos(math.radians(15)):
        raise CaptureError("Keep the camera upright and approximately level")
    valid = valid & np.isfinite(depth) & (depth > 0.3) & (depth < 6)
    return rgb, depth, k, up, valid


def unproject(x, y, z, k):
    return np.stack(((x - k[0, 2]) * z / k[0, 0],
                     (y - k[1, 2]) * z / k[1, 1], z), axis=-1)


def ellipse_cm(width: float, depth: float) -> float:
    a, b = width / 2, depth / 2
    return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))


def analyze(data: bytes, detector):
    rgb, depth, k, up, valid = load_bundle(data)
    prediction = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    if len(prediction.pose_landmarks) != 1 or not prediction.segmentation_masks:
        raise CaptureError("Capture exactly one person, standing with head and feet visible")
    h, w = depth.shape
    lm = prediction.pose_landmarks[0]
    points = np.array([[p.x * w, p.y * h] for p in lm])
    visibility = np.array([p.visibility or 0 for p in lm])
    mask = prediction.segmentation_masks[0].numpy_view().squeeze() > 0.5
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        raise CaptureError("Body outline not found")
    mask = labels == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    ys, xs = np.where(mask)
    if (ys.size < 500 or np.ptp(ys) < h * 0.55 or ys.min() < h * 0.01
            or ys.max() > h * 0.99 or xs.min() < w * 0.01 or xs.max() > w * 0.99):
        raise CaptureError("Include the entire body with a small margin, filling most of the image")
    if not any(visibility[a] > 0.6 and visibility[b] > 0.6 and points[b, 1] > points[a, 1] + h * 0.2
               for a, b in ((23, 27), (24, 28))):
        raise CaptureError("Stand upright with hips, straight legs and feet visible")
    coverage = np.count_nonzero(mask & valid) / np.count_nonzero(mask)
    if coverage < 0.85:
        raise CaptureError("Insufficient reliable body depth; repeat the scan")

    def point(x, y):
        x, y = int(round(x)), int(round(y))
        if not 0 <= x < w or not 0 <= y < h:
            raise CaptureError("Required body landmark lies outside the image")
        y0, y1, x0, x1 = max(0, y-2), min(h, y+3), max(0, x-2), min(w, x+3)
        good = (valid & mask)[y0:y1, x0:x1]
        values = depth[y0:y1, x0:x1][good]
        if len(values) < 3 or np.ptp(values) > 0.15:
            raise CaptureError("Unreliable depth at a required body point; repeat capture")
        return unproject(x, y, float(np.median(values)), k)

    # Robust height from surface points projected onto calibrated vertical.
    # Hair/shoes and missing extremity depth still bias this research estimate.
    for band in ((ys.min(), ys.min() + 8), (ys.max() - 8, ys.max() + 1)):
        region = mask[band[0]:band[1]]
        if np.count_nonzero((mask & valid)[band[0]:band[1]]) < 0.7 * np.count_nonzero(region):
            raise CaptureError("Head or foot depth is missing")
    yy, xx = np.where(mask & valid)
    heights = unproject(xx, yy, depth[yy, xx], k) @ up
    height = float(np.quantile(heights, 0.998) - np.quantile(heights, 0.002)) * 100
    shoulder = float(np.linalg.norm(point(*points[11]) - point(*points[12]))) * 100
    shoulder_mid = points[[11, 12]].mean(axis=0)
    hip_mid = points[[23, 24]].mean(axis=0)
    waist_y = int(round(shoulder_mid[1] + 0.62 * (hip_mid[1] - shoulder_mid[1])))
    torso_x = int(round(hip_mid[0]))
    widths = []
    for y in range(max(0, waist_y - 4), min(h, waist_y + 5)):
        edges = np.diff(np.pad(mask[y].astype(np.int16), (1, 1)))
        for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0] - 1):
            if start <= torso_x <= end:
                widths.append(float(np.linalg.norm(point(start, y) - point(end, y))) * 100)
                break
    if len(widths) < 5:
        raise CaptureError("Waist outline incomplete; keep arms away from torso")
    if not 50 <= height <= 250:
        raise CaptureError("Implausible height; check depth units and capture calibration")
    return dict(height_cm=height, shoulder_cm=shoulder, waist_axis_cm=float(np.median(widths)), depth_coverage=coverage)


def measure(front, side, detector):
    views = []
    for name, data in (("Front", front), ("Side", side)):
        try:
            views.append(analyze(data, detector))
        except CaptureError as exc:
            raise CaptureError(f"{name}: {exc}") from exc
    f, s = views
    if abs(f["height_cm"] - s["height_cm"]) > 5:
        raise CaptureError("Front and side height differ by more than 5 cm; recapture")
    return dict(height_cm=round(f["height_cm"], 1), shoulder_cm=round(f["shoulder_cm"], 1),
                waist_cm=round(ellipse_cm(f["waist_axis_cm"], s["waist_axis_cm"]), 1),
                status="review", algorithm=VERSION,
                note="Unvalidated depth-based estimates. Waist assumes an ellipse at a pose-derived level; verify against manual measurements.",
                diagnostics={"front": f, "side": s})


def worker(stop, detector):
    while not stop.is_set():
        try:
            # The row lock lasts until the result commits. If a worker crashes,
            # PostgreSQL releases it and the queued job is available again.
            with Sessions.begin() as db:
                job = db.scalar(select(Scan).where(Scan.status == "queued").order_by(Scan.created_at)
                                .with_for_update(skip_locked=True).limit(1))
                if job is not None:
                    try:
                        job.result = measure(job.front, job.side, detector)
                        job.status = "review"
                    except CaptureError as exc:
                        job.status, job.error = "recapture", str(exc)
                    except Exception:
                        log.exception("Scan processing failed: %s", job.id)
                        job.status, job.error = "failed", "Processing failed; contact the operator"
                    job.front = job.side = None  # Remove raw capture after processing.
                    job.completed_at = datetime.now(timezone.utc)
                    continue
        except Exception:
            log.exception("Worker database error")
        stop.wait(2)


@asynccontextmanager
async def lifespan(app):
    global engine, Sessions
    for name in ("CAPTURE_TOKEN", "ADMIN_TOKEN"):
        if len(os.environ.get(name, "")) < 24:
            raise RuntimeError(f"Set {name} to a random secret of at least 24 characters")
    if os.environ["CAPTURE_TOKEN"] == os.environ["ADMIN_TOKEN"]:
        raise RuntimeError("Use separate capture and administrator secrets")
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Set DATABASE_URL to postgresql+psycopg://user:password@host/database")
    model = Path(os.environ.get("POSE_MODEL_PATH", "models/pose_landmarker_full.task"))
    if not model.is_file():
        raise RuntimeError("Run python body_measure_handover.py --download-model first")
    engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    Sessions = sessionmaker(engine)
    Base.metadata.create_all(engine)
    detector = mp.tasks.vision.PoseLandmarker.create_from_options(mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model)), num_poses=2,
        running_mode=mp.tasks.vision.RunningMode.IMAGE, output_segmentation_masks=True))
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(stop, detector), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=30)
    if not thread.is_alive():
        detector.close()
        engine.dispose()


app = FastAPI(title="Phone RGB-D Measurement Research Prototype", lifespan=lifespan)


@app.get("/health")
def health():
    with engine.connect() as db:
        db.execute(text("SELECT 1"))
    return {"ok": True, "algorithm": VERSION}


@app.post("/api/scans", status_code=202, dependencies=[Depends(capture_access)])
def submit(external_id: str = Form(..., min_length=1, max_length=100),
           request_id: uuid.UUID = Form(...), receipt: str = Form(..., min_length=32, max_length=128),
           consent: bool = Form(...), front: UploadFile = File(...), side: UploadFile = File(...)):
    if not consent or not external_id.strip():
        raise HTTPException(400, "Consent and participant ID are required")
    blobs = [f.file.read(MAX_BYTES + 1) for f in (front, side)]
    if any(not b or len(b) > MAX_BYTES for b in blobs):
        raise HTTPException(413, "Each scan bundle must be nonempty and at most 20 MB")
    try:
        for blob in blobs:
            load_bundle(blob)
    except CaptureError as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = hashlib.sha256((external_id.strip() + '\0').encode() + hashlib.sha256(blobs[0]).digest()
                             + hashlib.sha256(blobs[1]).digest()).hexdigest()
    scan_id = str(request_id)
    with Sessions() as db:
        db.add(Scan(id=scan_id, external_id=external_id.strip(), receipt_hash=digest(receipt),
                    payload_hash=payload, front=blobs[0], side=blobs[1]))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            previous = db.get(Scan, scan_id)
            if previous is None or previous.receipt_hash != digest(receipt) or previous.payload_hash != payload:
                raise HTTPException(409, "Request ID already used; create a new submission")
    return {"id": scan_id, "status_url": f"/api/scans/{scan_id}"}


def public_scan(row):
    return dict(id=row.id, external_id=row.external_id, status=row.status, result=row.result,
                error=row.error, created_at=row.created_at, completed_at=row.completed_at)


@app.get("/api/scans/{scan_id}")
def status(scan_id: uuid.UUID, x_receipt: str | None = Header(None)):
    with Sessions() as db:
        row = db.get(Scan, str(scan_id))
        if row is None or not secrets.compare_digest(row.receipt_hash, digest(x_receipt or "")):
            raise HTTPException(404, "Scan not found")
        return public_scan(row)


@app.get("/api/admin/scans", dependencies=[Depends(admin_access)])
def listing(limit: int = 100, offset: int = 0):
    with Sessions() as db:
        rows = db.scalars(select(Scan).order_by(Scan.created_at.desc(), Scan.id)
                          .limit(min(max(limit, 1), 500)).offset(max(offset, 0)))
        return [public_scan(row) for row in rows]


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Body measurement prototype</title><style>
body{font:16px system-ui;max-width:650px;margin:30px auto;padding:20px;background:#f5f7fa}
label{display:block;margin-top:18px}input,button{box-sizing:border-box;width:100%;padding:12px}
button{margin-top:20px;background:#173e80;color:white;border:0;border-radius:8px}
pre{white-space:pre-wrap;background:white;padding:16px}small{display:block;color:#465267}
</style><h1>Body measurement prototype</h1>
<p>No height entry or printed marker. This research prototype processes calibrated depth scans.</p>
<p><b>Capture client required:</b> export front and side RGB-D bundles from a compatible
phone capture app. That mobile app is not included. Ordinary photos cannot be measured here.</p>
<form id="form"><label>Participant ID<input name="external_id" maxlength="100" required></label>
<label>Campaign access code<input id="token" type="password" required autocomplete="off"></label>
<label>Front scan bundle (.npz)<input name="front" type="file" accept=".npz" required></label>
<label>Side scan bundle (.npz)<input name="side" type="file" accept=".npz" required></label>
<label><input style="width:auto" name="consent" type="checkbox" value="true" required>
I agree to this scan being processed and its measurement results stored.</label>
<small>Raw scans are held temporarily for processing and removed from the application record
after processing. Database backups may retain earlier copies.</small>
<button id="submit">Submit scan</button></form><pre id="out" role="status">Waiting for a scan.</pre>
<script>
const form=document.getElementById('form'),out=document.getElementById('out');
let pending=null;
form.addEventListener('input',()=>{pending=null});
form.addEventListener('submit',async e=>{
 e.preventDefault();const button=document.getElementById('submit');button.disabled=true;
 try{
  if(!pending){const bytes=new Uint8Array(24);crypto.getRandomValues(bytes);
   pending={id:crypto.randomUUID(),receipt:Array.from(bytes,x=>x.toString(16).padStart(2,'0')).join('')};}
  const data=new FormData(form);data.set('request_id',pending.id);data.set('receipt',pending.receipt);
  out.textContent='Uploading...';
  const r=await fetch('/api/scans',{method:'POST',headers:{'X-Capture-Token':document.getElementById('token').value},body:data});
  const result=await r.json();if(!r.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));
  const current={...pending};
  for(let i=0;i<120;i++){
   const p=await fetch(result.status_url,{headers:{'X-Receipt':current.receipt}});
   if(!p.ok)throw new Error('Could not retrieve scan status');
   const status=await p.json();out.textContent=JSON.stringify(status,null,2);
   if(status.status!=='queued'){pending=null;return;}
   await new Promise(resolve=>setTimeout(resolve,3000));
  }
  out.textContent+='\\nStill queued. Submit unchanged files again to check the same request.';
 }catch(err){out.textContent=err.message;}finally{button.disabled=false;}
});
</script></html>"""


@app.get("/", response_class=HTMLResponse)
def home():
    return PAGE


def self_test():
    k = np.array([[100., 0, 50], [0, 100, 50], [0, 0, 1]])
    np.testing.assert_allclose(unproject(50, 50, 2., k), [0, 0, 2])
    np.testing.assert_allclose(unproject(100, 0, 2., k), [1, -1, 2])
    assert abs(ellipse_cm(20, 20) - math.pi * 20) < 1e-9
    try:
        load_bundle(b"not calibrated scan data")
        raise AssertionError("Invalid scan accepted")
    except CaptureError:
        pass
    def bundle(**overrides):
        arrays = dict(rgb=np.zeros((480, 480, 3), dtype=np.uint8),
                      depth_m=np.full((480, 480), 2., dtype=np.float32),
                      intrinsics=np.array([[500., 0, 240], [0, 500, 240], [0, 0, 1]]),
                      up=np.array([0., -1, 0]), valid=np.ones((480, 480), dtype=bool))
        arrays.update(overrides)
        output = io.BytesIO()
        np.savez_compressed(output, **arrays)
        return output.getvalue()
    # Synthetic data tests the transport contract only, NOT anthropometric accuracy.
    assert load_bundle(bundle())[0].shape == (480, 480, 3)
    for overrides in ({"depth_m": np.zeros((2, 2))}, {"up": np.array([0., 0, 0])},
                      {"intrinsics": np.eye(3) * -1}, {"valid": np.ones((480, 480))}):
        try:
            load_bundle(bundle(**overrides))
            raise AssertionError("Invalid calibration accepted")
        except CaptureError:
            pass
    print("PASS: unprojection, circular waist special case, bundle contract and invalid calibration rejection")


def download_model():
    import urllib.request
    target = Path(os.environ.get("POSE_MODEL_PATH", "models/pose_landmarker_full.task"))
    if target.is_file():
        print(f"Model already exists: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".download")
    urllib.request.urlretrieve(
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task", temp)
    temp.replace(target)
    print(f"Downloaded model: {target}")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    elif "--download-model" in sys.argv:
        download_model()
    else:
        import uvicorn
        logging.basicConfig(level=logging.INFO)
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
