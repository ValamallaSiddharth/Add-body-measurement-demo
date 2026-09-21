# Phone body measurements — code handover

> Archived alternative prototype. The active local college MVP now uses a printed 15 cm ArUco marker and runs with `docker compose up --build`. Follow the root README.md. This separate RGB-D experiment is not loaded by Docker or required for the demo.

The implementation is **body_measure_handover.py**, a single Python file containing the upload page, FastAPI routes, PostgreSQL table, durable job worker and metric depth-based measurement algorithm. It is independent of the existing app and does not alter its tables.

## What this actually delivers

- No entered height or printed marker.
- Front and side **calibrated RGB-D scan bundles**, not ordinary JPEG uploads.
- Height estimated from the vertical extent of depth points.
- Shoulder breadth estimated between pose landmarks in 3D.
- Waist circumference estimated as an ellipse from front and side silhouettes.
- PostgreSQL storage with unique request IDs, retry-safe submissions and review results.
- Access code for submitting scans, a private receipt for retrieving each result, and a separate administrator code for listing results.
- Background processing with PostgreSQL row locks. Unfinished jobs remain queued after a process crash.
- Raw bundles cleared from application rows after processing. Earlier copies may remain in database backups and database storage until reclaimed.

**This is a research backend prototype, not a finished product.** A native phone capture client exporting real calibrated depth is NOT included. Ordinary phone photographs are deliberately rejected. Browser file selection cannot manufacture depth data. Do not substitute a constant depth, an assumed adult height or invented camera calibration.

The compatible capture client must provide synchronized, undistorted RGB, aligned axial depth in metres, camera intrinsics, gravity/up direction and a reliable-depth mask, as documented at the top of the Python file. It must check device capabilities. Android ARCore Depth and Apple LiDAR scene depth are possible sources, with platform-specific coordinate and alignment conversion required.

## Run locally or on a Linux VM

Use Python 3.11 and PostgreSQL. From this project directory:

```bash
python -m pip install -r requirements.txt
python body_measure_handover.py --download-model
python body_measure_handover.py --self-test
```

If sharing only the Python file, its opening documentation includes the complete dependency-install command.

On Linux, set the connection and two DIFFERENT secrets, then run:

```bash
export DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE'
export CAPTURE_TOKEN='replace-with-a-random-secret-of-at-least-24-characters'
export ADMIN_TOKEN='replace-with-another-random-secret-of-at-least-24-characters'
python body_measure_handover.py
```

Replace placeholders with real values. URL-encode special characters in database credentials. Generate random secrets with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Use a database account permitted to create the new `rgbd_scans_v1` table on first startup.

Windows terminal equivalents for configuration:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE'
$env:CAPTURE_TOKEN = 'replace-with-a-random-secret-of-at-least-24-characters'
$env:ADMIN_TOKEN = 'replace-with-another-random-secret-of-at-least-24-characters'
python body_measure_handover.py
```

This standalone file reads environment variables, not the existing `.env`. The existing Compose database has no host port; do not assume it is reachable at localhost:5432. Run this backend on the database's container network or configure an accessible PostgreSQL instance. Avoid running both apps on port 8000; set `PORT=8001` if needed.

Open `http://localhost:8000`. API documentation is at `/docs`. On another phone, localhost means that phone: use the VM's HTTPS domain or the computer's reachable network address. The browser UI needs HTTPS (or localhost) for secure random request IDs.

## API

- `POST /api/scans`: multipart `external_id`, UUID `request_id`, random `receipt` of at least 32 characters, `consent=true`, front and side `.npz` files. Send `X-Capture-Token`. Returns HTTP 202 and a status URL.
- `GET /api/scans/{id}`: send `X-Receipt`. Returns queued, review, recapture or failed, with result/error.
- `GET /api/admin/scans?limit=100&offset=0`: send `X-Admin-Token`. Paginated results; no raw scan bytes.
- `GET /health`: checks database connectivity, not model accuracy or worker liveness.

The upload page generates request IDs and receipts. Retries from the same page with unchanged data reuse them. Reloading the page loses this in-memory receipt; a production client should securely persist its pending submission state.

## Azure deployment and the 8,000-person campaign

1. Install Docker or Python 3.11 with OpenCV runtime libraries (`libgl1`, `libglib2.0-0`) on an Ubuntu VM.
2. Start a single backend process under a service manager with automatic restart. The prototype has one processing thread; it does not claim 8,000 simultaneous scans.
3. Place it behind an HTTPS reverse proxy. Limit request size to approximately 42 MB and set upload timeouts. Add submission rate limits before public exposure. Only expose HTTPS publicly; keep PostgreSQL private.
4. Prefer managed PostgreSQL with TLS, backups and restore testing. Use migrations before schema changes; `create_all` initializes this table but does not migrate existing schemas.
5. For the campaign, replace the shared access code with individually authenticated users/operators. Add retention cleanup for never-processed uploads, audit logs, worker monitoring and administrative review actions.
6. Move raw uploads from PostgreSQL into private Blob Storage before large deployment. The current temporary PostgreSQL byte storage simplifies the one-file demonstration but is unsuitable for an uncontrolled upload backlog: 8,000 maximum-sized submissions could approach 320 GB before processing, excluding database overhead/backups.
7. Validate on a representative pilot against manual height, anatomical shoulder breadth and waist circumference. Specify anatomical definitions and acceptable error before rollout. Capture framing checks do not prove correct posture or front/side orientation. Hair, clothes, pose and depth quality can bias results.
8. Benchmark time per scan and peak arrival rate to size processing workers. Deploy additional workers with controlled schema initialization after testing, or a dedicated queue architecture. Do not select VM capacity from participant count alone.

## Verification status

Frontend JavaScript is checked separately in the handover preparation. The file includes executable numerical and input-contract self-tests. Python, PostgreSQL integration, real RGB-D scans and measurement accuracy have NOT been verified in the authoring session because no working Python/Docker runtime or real calibrated depth sample was available. Execute the tests and a real-device pilot before treating this as working production software.

## Official references

- ARCore Depth: https://developers.google.com/ar/develop/depth
- Apple scene-depth capture: https://developer.apple.com/documentation/ARKit/displaying-a-point-cloud-using-scene-depth
- Azure web/queue/worker architecture: https://learn.microsoft.com/en-us/azure/architecture/guide/architecture-styles/web-queue-worker
- PostgreSQL backups on Azure: https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore
