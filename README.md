# reScrub

reScrub reviews recordings of practice surgeries for breaks in sterile technique. From one
angled camera it:
- labels everyone anonymously ("Person 1", "Person 2", …);
- tracks their arms and hands;
- marks sterile and non-sterile surfaces;
- proposes timestamped moments for an instructor or trainee to review: a hand touching
  something non-sterile and then something sterile, or an unscrubbed person touching the
  sterile field.

Live at **https://rescrub.health**.

```
                         ┌──────────── Web server (Vultr) ─────────────┐
 Browser ── HTTPS ──▶    │ Caddy ─┬─ /      Next.js website            │
 rescrub.health          │        └─ /api   FastAPI API (sign-in,      │
                         │                  uploads, results)          │
                         └──────┬───────────────────────┬──────────────┘
                                ▼                       ▼
                   Tiger Cloud (TimescaleDB)    Vultr Object Storage
                   accounts, jobs, tracking,    labeled videos
                   findings                     (private)
                                ▲                       ▲
                         ┌──────┴───────────────────────┴──────────────┐
                         │ GPU server (Vultr): worker — labeling,      │
                         │ tracking, breach analysis; model training   │
                         └─────────────────────────────────────────────┘
```

## Repository

| Path | What it is |
| --- | --- |
| [`web/`](web/README.md) | Website: sign-in, upload, review workspace with labeled video, timeline and findings |
| [`backend/`](backend/README.md) | API, GPU worker, labeling and tracking pipeline, breach analysis, accounts CLI |
| [`backend/training/`](backend/training/README.md) | Training the OR models on the MM-OR dataset, and scoring against labeled breaches |
| [`deploy/`](deploy/) | Caddy config and update scripts for the servers |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Setting up Tiger Cloud, both Vultr servers and the domain; training; operations |
| [`docs/API.md`](docs/API.md) | API reference |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Security model and how to verify it |

## Development

```bash
# Backend API (needs PostgreSQL with pgvector; see backend/README.md)
cd backend && pip install -r requirements-dev.txt
DATABASE_URL=postgresql://… STORAGE_BACKEND=local CORS_ORIGINS=http://localhost:3000 COOKIE_SECURE=false \
  uvicorn rescrub.main:app --port 8000 --root-path /api
python -m rescrub.accounts add you@example.com     # prints a password

# Website, forwarding /api to the local backend
cd web && npm ci
NEXT_PUBLIC_API_BASE_URL=/api API_INTERNAL_URL=http://127.0.0.1:8000 API_DEV_PROXY=http://127.0.0.1:8000 npm run dev
```

CI runs on every pull request:
- backend lint and tests against Postgres;
- website lint, type check and build;
- a dependency audit;
- a secret scan of every commit.
