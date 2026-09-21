# Body Measurement System — local college MVP

The app opens in **Demo mode** for a college presentation. Enter a Person ID and click **Run demo & save**. Front and side photos are optional; no marker or known height is needed. It displays fixed, clearly labelled sample height, shoulder width, waist circumference and confidence, saves them in PostgreSQL, and supports history and lookup. These values are simulated, not measurements of the uploaded person.

Demo records use the separate `demo_measurements` table. The demo API is `POST /api/demo/measure`; add `?mode=demo` to the measurement list or person lookup endpoints to retrieve demos. Existing endpoints default to real records.

For actual image-based estimates, select **Real measurement**. That mode estimates **height**, **shoulder width** and **waist circumference** from two phone photos using a printed **15 cm × 15 cm ArUco marker** for centimetre scale. The marker and capture instructions below apply only to real mode. You do not enter the person's height.

Everything runs in Docker: FastAPI, OpenCV, MediaPipe and PostgreSQL. No Python or database installation on Windows is required. This README describes the active local demo, not the separate old RGB-D handover experiment.

## 1. Start the app

1. Install Docker Desktop with its WSL 2 backend and Linux containers enabled.
2. Open Docker Desktop. Wait until it says **Engine running**.
3. Open a terminal in VS Code or Windows Terminal and run:

```powershell
cd path/to/body_measure_app
docker compose up --build
```

The first build downloads dependencies and a MediaPipe model. Internet is needed for this build. Wait for **Application startup complete**. Leave this terminal open.

Replace the example folder path with your cloned project folder (on the original Windows machine: `D:\body_measure_app`). On a Linux machine with Docker Engine and the Compose plugin installed, use the same `docker compose up --build` command.

Open **http://localhost:8000** on your laptop.

- Health: http://localhost:8000/health
- API documentation: http://localhost:8000/docs
- Printable marker: http://localhost:8000/marker

No `.env` copy is required for the provided local defaults. An existing `DATABASE_URL` in `.env` overrides the default connection. Keep the supplied `db` hostname when using the bundled PostgreSQL service. Existing database credentials/volumes are preserved; changing environment credentials does not change passwords in an already initialized database.

The Compose service names remain **api** and **db**, to preserve the existing installation. New tables are created automatically. Existing `measurements` records remain available.

## 2. Open it on your phone

Connect your phone and laptop to the **same Wi-Fi**. In a second terminal tab run:

```powershell
ipconfig
```

Find the **IPv4 Address** under your active **Wireless LAN adapter Wi-Fi**. For example, if it is `192.168.1.10`, open this in your phone browser:

```text
http://192.168.1.10:8000
```

Use your actual address, not the example. Do not use `localhost` on the phone; that points to the phone itself.

If Windows Firewall asks, allow Docker access on **Private networks**. If the page does not load, check the Wi-Fi, VPN, firewall and router guest-network/client-isolation settings. Do not expose this unauthenticated college demo to the public internet.

The page uses file inputs with `capture="environment"`, so supported phone browsers offer the rear camera without requiring JavaScript camera streaming over HTTPS. Exact camera-picker behavior depends on the browser. Separate **Choose saved photo** controls always allow normal uploads.

## 3. Print the marker

Open **http://localhost:8000/marker** and click **Print marker**.

- Use A4 paper, **100% / Actual size**, not Fit to page.
- Turn browser print headers and footers off.
- Measure the **outer black square** with a ruler. It must be exactly **15 cm × 15 cm**. Do not include the white margin.
- Keep the white border around the marker and mount it flat on cardboard.
- Marker dictionary: **DICT_4X4_50**, ID **0**. An ordinary QR code does not work.
- Place it upright beside the person's body, facing the camera. It must be approximately the same distance from the camera as the body, not on a wall farther behind.

If you want generated files as well, run in a second terminal:

```powershell
docker compose exec api python scripts/generate_marker.py
docker compose cp api:/app/data/images/marker ./generated-marker
```

Open `generated-marker/print_marker.html`. It is self-contained and prints the same correctly sized SVG. A PNG and SVG are also included. Prefer the HTML print page because image-viewer scaling may resize the PNG.

## 4. Test one person

Select **Real measurement** for the steps below. For the marker-free presentation, keep **Demo** selected, enter an ID, optionally attach photos, and click **Run demo & save**.

1. Enter a unique ID, for example `P001`.
2. Take a **front photo**: stand straight facing the camera, shoes off, fitted clothes, arms slightly away from the torso. Include the whole head and feet, the marker, and a small margin around the body.
3. Take a **side photo**: stand fully sideways, keeping arms clear of the waist and head/feet/marker visible. Keep the camera approximately level, at waist/chest height, and a similar distance away.
4. Use a plain background and good lighting. Only one person should be in frame.
5. Select the photos on the page. Check their previews.
6. Click **Measure body**. Wait for **Processing images…** to finish.
7. A valid result is saved in PostgreSQL and shown in the results card and recent-records table. Click **Measure another person** to reset the form.

JPEG, PNG and WebP are supported. Maximum: **15 MB per photo**, **25 megapixels**, and at least **480 pixels on the shorter side**. EXIF rotation is applied. Large images are resized internally, and the marker is measured at the same resized scale. HEIC is not supported: export JPEG, or use the iPhone camera's Most Compatible setting.

IDs are case-sensitive and trimmed. Duplicate IDs return an explanatory error, rather than creating another measurement. Look up an existing ID using **Find**, or use an ID such as `P001-R2` for a repeat capture. Historical duplicate records remain readable.

## How measurement works

```text
Phone camera / saved photos
        -> FastAPI validation
        -> ArUco scale for EACH photo
        -> MediaPipe pose + body segmentation
        -> height / shoulder / ellipse waist estimates
        -> PostgreSQL + persistent image volume
        -> results and saved-record lookup
```

- **Scale:** average marker side in pixels divided by 15 cm. Front and side photos each get their own scale.
- **Height:** highest to lowest point of the front body mask, divided by the front scale. Pose-derived crown/foot estimates are a lower-confidence fallback; they are not shoulder-to-ankle height.
- **Shoulders:** distance between the front pose's shoulder landmarks, divided by the front scale.
- **Waist:** find the silhouette at 60% of the shoulder-to-hip distance (40% upward from hips). Front width and side depth use their respective scales. Ramanujan's ellipse approximation estimates circumference.
- **Fallback:** when MediaPipe segmentation is absent, pose-seeded GrabCut attempts an outline. It still must pass validation. If no usable waist mask exists, the capture is rejected.
- **Confidence:** a heuristic combining marker geometry/size, pose visibility, silhouette method, framing, and agreement between views. It is NOT a calibrated accuracy probability.

Scores **0.85–1.00** are `good`, **0.65–0.849** are `review`. Below **0.65**, or if capture validation fails, the app requests a retake and saves nothing. Fallback estimates are capped at review quality.

## Files and database

- `app/main.py`: API, startup, upload handling, persistence and errors.
- `app/db.py`, `models.py`, `schemas.py`: database connection and records.
- `app/services/`: ArUco detection, pose, segmentation and calculations.
- `app/static/`: mobile interface, previews, results and history.
- `scripts/generate_marker.py`: printable marker files.
- `scripts/download_model.py`: versioned model download with retries.
- `tests/`: numerical, capture-validation and PostgreSQL/API tests.
- `Dockerfile`, `docker-compose.yml`: Python 3.11 app and PostgreSQL 17.

To preserve existing data, the SQL column `external_id` is exposed as `person_id` in the API. The table uses numeric Float columns for estimates, an indexed person ID, confidence, status, notes, UTC date and image filenames. The original image-path column names are retained for compatibility.

New successful photos are normalized to JPEG, given server-generated UUID filenames, and saved under **`/app/data/images/uploads`** in the **image_data** Docker volume. They are not exposed through a public static route. PostgreSQL stores filenames, never raw image bytes. Invalid photos are not retained. The **pgdata** volume persists database records.

The old `body_measure_handover.py` and `HANDOVER.md` are archived alternatives, not required or run by Docker.

## API

| Method | URL | Purpose |
|---|---|---|
| GET | `/` | Frontend |
| GET | `/health` | API, database and pose-model readiness |
| GET | `/marker` | Printable 15 cm marker |
| POST | `/api/measure` | Multipart `person_id`, `front_image`, `side_image` |
| POST | `/api/demo/measure` | Sample results; `person_id` required, photos optional |
| GET | `/api/measurements?limit=100&offset=0` | Paginated saved records |
| GET | `/api/measurements/{person_id}` | Records for a person, or 404 |

Success returns `success`, `mode`, `person_id`, `measurements`, `confidence`, `status`, `notes`, `created_at` and the saved record. Add `?mode=demo` to list/lookup URLs for demo records; the default is real records. Errors return `success: false` and a useful `error`. The old POST `/api/measurements` remains as a compatibility alias using `external_id`, `front`, `side`.

## Stop, restart and reset

Stop foreground logs and the attached services with **Ctrl+C**, or run:

```powershell
docker compose down
```

This keeps your saved records and photos. Start in the background if preferred:

```powershell
docker compose up -d --build
```

View logs:

```powershell
docker compose logs --tail=100 api
docker compose logs --tail=100 db
```

**Destructive reset — only if you intentionally want to erase all records and saved photos:**

```powershell
docker compose down -v
```

After that, `docker compose up --build` creates a fresh database.

## Run the tests in Docker

### Current verification status

- Python syntax checked successfully across all 18 app/script/test Python files.
- JavaScript syntax and automated frontend behavior checks passed (previews, validation, result display, duplicate errors and reset).
- Docker Compose configuration and a full Docker build passed. Both containers started and reported healthy.
- `pip check` found no broken dependency requirements.
- All **22 Python tests passed** inside the built container, including the isolated PostgreSQL/API integration suite, demo/real record separation, optional demo uploads, IDs containing slashes, ArUco calibration, numerical calculations, capture validation, safe image saving, duplicate-ID rejection, database-error cleanup and real pose-model execution on an empty image.
- The live homepage and health endpoint returned HTTP 200; health reported the database connected and pose model ready.
- Desktop and an exact 390-pixel mobile layout were visually checked in headless Chrome. The mobile page had no document-level horizontal overflow. The marker page was also inspected, and printable files were generated.
- **Real-person measurement accuracy and an actual phone-camera session still need testing.** Synthetic success fixtures test the pipeline without claiming anthropometric accuracy. No printed marker can be physically size-verified by software: check it with a ruler.

Do not use `docker compose down -v` for routine restarts; it erases stored data.

If Node.js is already installed, frontend behavior checks can also run with `node tests/test_frontend.cjs`. Node is not needed to run the app.

Numerical and capture-validation tests (no Windows Python installation):

```powershell
docker compose exec api python -m unittest discover -s tests -p test_measure.py -v
```

Integration tests use a separate test database to avoid touching your saved people:

```powershell
docker compose exec db createdb -U measure measurements_test
docker compose exec -e DATABASE_URL=postgresql+psycopg://measure:measurepass@db:5432/measurements_test -e RUN_DB_TESTS=1 api python -m unittest discover -s tests -v
```

If `measurements_test` already exists, skip the `createdb` command. The tests use generated fixtures and mocked pose/measurement outputs for reproducible success cases. They also exercise the real pose model on a blank image. Passing tests verifies code behavior, not real-person accuracy.

## Limitations

This is a local college MVP, not a medical device or production deployment. It estimates apparent shoulder-landmark distance and silhouette-based waist circumference. Hair, shoes, loose clothes, arms, tilted markers, lens distortion, perspective and anatomical waist placement affect results. Capture checks are heuristics and can miss incorrect poses or other people. Compare against manual measurements on a representative sample before using it for 8,000 people. This demo serializes measurement processing and returns a retry message when busy; 8,000 concurrent users are not supported or tested. No Azure setup, account system, or real-world accuracy claim is included.

Python 3.11 and the pinned MediaPipe/OpenCV/NumPy versions match the tested container combination. The single `opencv-contrib-python` wheel supplies ArUco and satisfies MediaPipe's dependency; Docker installs its Linux runtime libraries. Do not additionally install a second headless/GUI OpenCV wheel into the same environment.
