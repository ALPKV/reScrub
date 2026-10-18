# reScrub backend

The API (web server) and the GPU worker (analysis server). For each uploaded recording the
worker produces:

- **People:** everyone is labeled anonymously (`Person 1`, `Person 2`, …) and keeps the label
  for the whole recording, and is classified as scrubbed (sterile) or unscrubbed.
- **Arms and hands:** shoulders, elbows, wrists and hips, plus the hand wrist and five
  fingertips per hand, every frame.
- **Surfaces:** sterile, non-sterile, patient and neutral areas (drapes, instrument tables,
  instruments, undraped tables, equipment, the exposed patient).
- **Proximity features:** for every hand or arm point near a surface: distance, inside or
  outside, relative depth gap and speed, with a `candidate_contact` flag.
- **Findings:** timestamped breaks in sterile technique, each with an evidence chain.
- **Labeled video:** H.264 MP4 with boxes, `Person N` tags, arm and hand skeletons, colored
  surfaces and a timestamp on every frame.

Everything is timestamped and stored in Tiger Cloud (TimescaleDB + pgvector); videos are in
Vultr Object Storage.

```
Website ── POST /api/sessions ──▶ API ──▶ Object Storage (upload)
                                   └────▶ Tiger Cloud: sessions (state = queued)
GPU worker ◀── claims the job (FOR UPDATE SKIP LOCKED) ──┘
  1. YOLO26-pose + BoT-SORT          → people, track IDs, arm keypoints
  2. ReID embeddings + gating        → stable "Person N"
  3. RTMW (rtmlib)                   → hand and fingertip points
  4. OR segmentation model           → surfaces + scrubbed/unscrubbed roles (or hand-drawn zones)
  5. Depth Anything V2               → relative depth (hover vs. touch)
  6. Proximity features → breach analysis → findings
  7. Render + ffmpeg                 → labeled video to Object Storage
  └──▶ Tiger Cloud: people, detections, keypoints, surfaces, proximity_events, findings
Website ◀── GET /api/sessions/{id} ── completed: findings + annotatedVideoUrl
```

## Layout

| Path | What it does |
| --- | --- |
| `rescrub/api.py` | FastAPI app ([docs/API.md](../docs/API.md)) |
| `rescrub/auth.py` | Accounts and sign-in: Argon2id, hashed session tokens, Origin check |
| `rescrub/accounts.py` | Admin CLI: add, reset, disable, enable, list, delete accounts |
| `rescrub/worker.py` | Job loop: claims queued recordings, runs the pipeline and analysis, uploads the labeled video |
| `rescrub/pipeline/run.py` | Orchestrates the stages; also a CLI for single videos |
| `rescrub/pipeline/people.py`, `identity.py` | Tracking and stable `Person N` labels (with scrubbed/unscrubbed votes) |
| `rescrub/pipeline/hands.py`, `depth.py` | Hand keypoints (RTMW) and relative depth |
| `rescrub/pipeline/surfaces.py` | Surface and role segmentation, hand-drawn zones, stable surface IDs |
| `rescrub/pipeline/proximity.py`, `render.py` | Hand-to-surface features; labeled video |
| `rescrub/analysis/breaches.py` | Breach rules → findings |
| `rescrub/db.py`, `storage.py` | Tiger Cloud access (COPY for bulk rows); Object Storage with HTTP Range streaming |
| `config/surfaces_mmor.yaml` | Production surface/role config for the trained model (class → designation) |
| `config/surfaces.yaml` | Fallback config for the open-vocabulary model, before training |
| `config/botsort_reid.yaml` | Tracker tuning for a fixed camera and identical gowns |
| `db/schema.sql`, `db/app_role.sql` | Tables, hypertables and compression; the least-privilege app user |
| `Dockerfile.api`, `Dockerfile`, `docker-compose.yml` | API image (web server), worker image (GPU server), profiles `api` / `worker` |
| `training/` | MM-OR training and evaluation ([training/README.md](training/README.md)) |

Deployment: [docs/DEPLOY.md](../docs/DEPLOY.md).

## Breach rules

`rescrub/analysis/breaches.py` groups per-frame contacts into events and applies sterile
technique. The rules follow the patterns of the sterility breaches labeled in the MM-OR dataset.

| Finding | Rule |
| --- | --- |
| "Person N's hand touches *non-sterile*, then *sterile*" | A scrubbed hand contacts a non-sterile surface and then a sterile one (one chained finding) |
| "Person N's hand touches *non-sterile*" | A scrubbed hand or forearm contacts a non-sterile surface |
| "Unscrubbed Person N touches *sterile/patient*" | An unscrubbed person contacts the sterile field, an instrument, or the patient |

Contacts with `neutral` surfaces (e.g. the robot) are never findings. Event grouping adapts
to the frame rate. Re-run on stored data with
`python -m rescrub.analysis.breaches --session <id> --save`.

## Surfaces and hand-drawn zones

Production uses the trained OR model (`SURFACE_CONFIG=config/surfaces_mmor.yaml`); each class's
designation is set in that file and can be changed without retraining. For a fixed camera,
hand-drawn zones can replace automatic detection for a room (`DEFAULT_ZONES_FILE`) or per upload
(`zones` field, format in [docs/API.md](../docs/API.md)).

## Data in Tiger Cloud

| Table | One row per | Key columns |
| --- | --- | --- |
| `users`, `auth_sessions` | account; signed-in browser | email, Argon2id hash, role, disabled; SHA-256 of the cookie, expiry |
| `sessions` | recording | `owner_id`, `state`, `progress`, `fps`, `width`, `height`, `annotated_key` |
| `people` | person | `person_label`, first/last seen, `designation` (scrubbed = sterile), `mean_embedding` |
| `track_identity` | tracker ID | which tracker IDs were merged into each person |
| `detections` (hypertable) | person × frame | box, confidence |
| `keypoints` (hypertable) | point × person × frame | `part`, `x`, `y`, `conf`, `depth` |
| `surfaces` | surface × keyframe | `label`, `designation`, `polygon` |
| `proximity_events` (hypertable) | point × surface × frame (within `NEAR_PX`) | `pixel_dist`, `inside_mask`, `depth_gap`, `speed_px_s`, `candidate_contact` |
| `findings` | finding | the website's `Finding` JSON and the reviewer's decision |

`video_sec` is seconds from the start of the recording; `ts` (= `recorded_at + video_sec`)
partitions the hypertables.

```sql
-- Right index fingertip of Person 2 over time
SELECT video_sec, x, y, depth FROM keypoints
WHERE session_id = $1 AND person_label = 'Person 2' AND part = 'r_index_tip' ORDER BY ts;

-- Candidate non-sterile contacts in 0.5 s buckets
SELECT time_bucket('0.5 seconds', ts) AS bucket, person_label, surface_id, min(video_sec) AS start_sec, count(*)
FROM proximity_events
WHERE session_id = $1 AND candidate_contact AND designation = 'nonsterile'
GROUP BY 1, 2, 3 ORDER BY 1;
```

## Development

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install torch torchvision          # worker and pipeline only; the API doesn't need them
pip install -r requirements-dev.txt    # macOS/ARM: also pip install onnxruntime

# One video, no database: JSON lines + labeled video in out/
python -m rescrub.pipeline.run clip.mp4 --out out/ [--zones zones.json]

# Full stack against a local PostgreSQL with pgvector (or: docker compose --profile localdb up -d)
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/rescrub STORAGE_BACKEND=local \
       CORS_ORIGINS=http://localhost:3000 COOKIE_SECURE=false
python -m rescrub.init_db
python -m rescrub.accounts add you@example.com
uvicorn rescrub.main:app --port 8000 --root-path /api &
python -m rescrub.worker
```

`POSE_MODEL=yolo26n-pose.pt` and `FRAME_STRIDE=2` speed up CPU runs.

Tests: `TEST_DATABASE_URL=postgresql://…/rescrub_test pytest` (uses a disposable database).
