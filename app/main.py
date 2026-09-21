from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
from threading import BoundedSemaphore
import time
import uuid
from typing import Literal

import cv2
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import settings
from .db import Base, engine, SessionLocal
from .models import Measurement, DemoMeasurement
from .schemas import MeasurementOut
from .measure import decode_image, measure_person, MeasurementError
from .services.aruco_service import marker_page, marker_svg, marker_image
from .services.pose_service import initialize_pose, close_pose
from .upload_limit import UploadLimitMiddleware

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
processing_slot = BoundedSemaphore(1)


@asynccontextmanager
async def lifespan(app):
    for attempt in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except SQLAlchemyError:
            if attempt == 29:
                raise RuntimeError("PostgreSQL unavailable. Check docker compose logs db.") from None
            log.warning("Waiting for PostgreSQL (%s/30)", attempt + 1)
            time.sleep(2)
    Path(settings.image_dir).mkdir(parents=True, exist_ok=True)
    initialize_pose()
    app.state.model_ready = True
    yield
    app.state.model_ready = False
    close_pose()
    engine.dispose()


app = FastAPI(title="Body Measurement System", version="1.0.0", lifespan=lifespan)
app.add_middleware(UploadLimitMiddleware, max_bytes=2*settings.max_upload_bytes+1_000_000)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"success": False, "error": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def input_error(request, exc):
    fields = ", ".join(sorted({str(e["loc"][-1]) for e in exc.errors()}))
    return JSONResponse({"success": False, "error": f"Missing or invalid input: {fields}."}, status_code=422)


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    log.exception("Database operation failed", exc_info=exc)
    return JSONResponse({"success": False, "error": "Database unavailable. Check the db container and try again."}, status_code=503)


def get_db():
    with SessionLocal() as db:
        yield db


@app.get("/health")
def health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        ready = getattr(app.state, "model_ready", False)
        return JSONResponse({"ok": ready, "database": "connected", "pose_model": "ready" if ready else "unavailable"}, status_code=200 if ready else 503)
    except SQLAlchemyError:
        return JSONResponse({"ok": False, "database": "unavailable"}, status_code=503)


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/marker", response_class=HTMLResponse)
def printable_marker():
    return marker_page()


@app.get("/marker.svg")
def download_marker():
    return Response(marker_svg(), media_type="image/svg+xml", headers={"Content-Disposition": 'attachment; filename="aruco_15cm.svg"'})


@app.get("/marker.png")
def download_marker_png():
    marker = cv2.copyMakeBorder(marker_image(), 75, 75, 75, 75, cv2.BORDER_CONSTANT, value=255)
    ok, data = cv2.imencode(".png", marker)
    if not ok:
        raise HTTPException(500, "Marker generation failed")
    return Response(data.tobytes(), media_type="image/png")


def read_photo(upload, view):
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(415, f"{view}: use JPEG, PNG or WebP. Convert HEIC to JPEG first.")
    if upload.content_type not in {None, "", "application/octet-stream", "image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, f"{view}: unsupported image type.")
    data = upload.file.read(settings.max_upload_bytes+1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"{view}: photo exceeds 15 MB.")
    try:
        return decode_image(data)
    except MeasurementError as exc:
        raise HTTPException(422, f"{view}: {exc}") from exc


def response_record(rec):
    result = MeasurementOut.model_validate(rec).model_dump(mode="json")
    result["mode"] = "demo" if isinstance(rec, DemoMeasurement) else "real"
    return result


def create_record(person_id, front_image, side_image, db, demo=False):
    model = DemoMeasurement if demo else Measurement
    person_id = person_id.strip()
    if not person_id or len(person_id) > 100 or any(ord(c) < 32 for c in person_id):
        raise HTTPException(400, "Person ID must contain 1-100 characters, without control characters.")
    # Serialize inserts for this ID, including requests from another app worker.
    # Existing data is preserved; no destructive unique-index migration is needed.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:person_id))"), {"person_id": ("demo:" if demo else "real:") + person_id})
    previous = db.scalar(select(model).where(model.external_id == person_id).limit(1))
    if previous:
        raise HTTPException(409, "This Person ID already has a saved measurement. Look it up below, or use a new ID for a repeat capture.")
    if not processing_slot.acquire(blocking=False):
        raise HTTPException(429, "Another capture is processing. Wait a few seconds and try again.", headers={"Retry-After": "5"})
    written = []
    try:
        front = read_photo(front_image, "Front photo") if front_image is not None else None
        side = read_photo(side_image, "Side photo") if side_image is not None else None
        if demo:
            # Fixed presentation data, never inferred from the uploaded person.
            result = {"height_cm": 174.6, "shoulder_cm": 44.2, "waist_cm": 88.7,
                      "front_waist_width_cm": 31.5, "side_waist_depth_cm": 24.8,
                      "confidence": 0.88, "status": "demo",
                      "notes": "DEMO ONLY: fixed sample values, not measurements of the uploaded person. The 88% score is illustrative. Saved separately from real measurements."}
        else:
            result = measure_person(front, side)
        record_id = str(uuid.uuid4())
        filenames = [None, None]
        if settings.store_images:
            for index, (name, photo) in enumerate((("front", front), ("side", side))):
                if photo is None:
                    continue
                filenames[index] = f"{record_id}_{name}.jpg"
                path = Path(settings.image_dir) / filenames[index]
                ok, encoded = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 92])
                if not ok:
                    raise OSError("Image encoding failed")
                written.append(path)
                path.write_bytes(encoded.tobytes())
        rec = model(id=record_id, external_id=person_id,
                          **{key: result[key] for key in ("height_cm", "shoulder_cm", "waist_cm", "front_waist_width_cm", "side_waist_depth_cm", "confidence", "status", "notes")},
                          front_image_path=filenames[0], side_image_path=filenames[1],
                          created_at=datetime.now(timezone.utc))
        db.add(rec)
        db.flush()
        record = response_record(rec)
        db.commit()
        return {"success": True, "person_id": person_id, "mode": "demo" if demo else "real",
                "measurements": {key: result[key] for key in ("height_cm", "shoulder_cm", "waist_cm", "front_waist_width_cm", "side_waist_depth_cm")},
                "confidence": result["confidence"], "status": result["status"], "notes": result["notes"],
                "created_at": record["created_at"], "id": record_id, "record": record}
    except Exception as exc:
        db.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, MeasurementError):
            raise HTTPException(422, str(exc)) from exc
        if isinstance(exc, SQLAlchemyError):
            raise
        log.exception("Measurement failed")
        if isinstance(exc, OSError):
            raise HTTPException(500, "Could not save the images. Check the upload volume and free disk space.") from exc
        raise HTTPException(500, "Image processing failed. Try another photo; details are in the API container logs.") from exc
    finally:
        processing_slot.release()


@app.post("/api/measure")
def create_measurement(person_id: str = Form(...), front_image: UploadFile = File(...),
                       side_image: UploadFile = File(...), db: Session = Depends(get_db)):
    return create_record(person_id, front_image, side_image, db)


@app.post("/api/measurements", include_in_schema=False)
def legacy_measurement(external_id: str = Form(...), front: UploadFile = File(...),
                       side: UploadFile = File(...), db: Session = Depends(get_db)):
    return create_record(external_id, front, side, db)["record"]


@app.post("/api/demo/measure")
def create_demo(person_id: str = Form(...), front_image: UploadFile | None = File(None),
                side_image: UploadFile | None = File(None), db: Session = Depends(get_db)):
    """Save labelled sample results, with optional photos and no computer vision."""
    return create_record(person_id, front_image, side_image, db, demo=True)


@app.get("/api/measurements")
def list_measurements(limit: int = 100, offset: int = 0, mode: Literal["real", "demo"] = "real", db: Session = Depends(get_db)):
    model = DemoMeasurement if mode == "demo" else Measurement
    rows = db.scalars(select(model).order_by(model.created_at.desc(), model.id)
                      .limit(min(max(limit, 1), 500)).offset(max(offset, 0)))
    return [response_record(row) for row in rows]


@app.get("/api/measurements/{person_id:path}")
def person_measurements(person_id: str, mode: Literal["real", "demo"] = "real", db: Session = Depends(get_db)):
    model = DemoMeasurement if mode == "demo" else Measurement
    rows = db.scalars(select(model).where(model.external_id == person_id.strip())
                      .order_by(model.created_at.desc())).all()
    if not rows:
        raise HTTPException(404, "No measurements found for this Person ID.")
    return [response_record(row) for row in rows]
