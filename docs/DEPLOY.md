# Deploying reScrub

Production runs on four services:

| Service | Runs | Reached by |
| --- | --- | --- |
| **Web server** (Vultr Cloud Compute) | Caddy (HTTPS), the Next.js website, the FastAPI API | Browsers, at `https://rescrub.health` |
| **GPU server** (Vultr Cloud GPU) | The analysis worker (labeling, tracking, breach analysis) and model training | SSH only |
| **Tiger Cloud** (TimescaleDB) | Accounts, jobs, tracking data, findings | Both servers |
| **Vultr Object Storage** | Uploaded recordings and labeled videos (private bucket) | Both servers |

```
Browser ──HTTPS──▶ rescrub.health ─────────── WEB SERVER (Vultr Cloud Compute) ───────────┐
                   │ Caddy :443                                                             │
                   │   /api/*  ──▶ FastAPI API   127.0.0.1:8000 (Docker)  sign-in, uploads   │
                   │   /*      ──▶ Next.js site  127.0.0.1:3000 (pm2)     pages              │
                   └──────────────────┬──────────────────────────────┬───────────────────────┘
                                      │ accounts, jobs, findings     │ recordings, labeled videos
                                      ▼                              ▼
                        Tiger Cloud (TimescaleDB)          Vultr Object Storage (private)
                                      ▲                              ▲
                   ┌──────────────────┴──── GPU SERVER (Vultr Cloud GPU) ───┴──────────────────┐
                   │ Worker (Docker): labeling, tracking, breach analysis — always running    │
                   │ Training on MM-OR (same image, block-storage volume)                     │
                   │ Inbound: SSH only. It pulls jobs from Tiger Cloud.                       │
                   └──────────────────────────────────────────────────────────────────────────┘
```

How a recording flows:
1. The browser only talks to `https://rescrub.health`. The API is under `/api` on the same
   origin, so the sign-in cookie is first-party and no cross-site requests are needed.
2. The API saves the upload to Object Storage and adds a `queued` job row in Tiger Cloud.
3. The GPU worker claims the job, downloads the video, writes tracking data and findings to
   Tiger Cloud, and uploads the labeled video.
4. The website polls `/api/sessions/<id>` and plays `/api/sessions/<id>/annotated.mp4`.
   Only the account that uploaded a recording can see it.

**Order:** 1 GitHub → 2 Tiger Cloud → 3 Object Storage → 4 Domain → 5 Web server →
6 Accounts → 7 GPU server → 8 Training → 9 Verify. Section 10 covers day-to-day operations.

**Where secrets live.** Every credential goes in exactly one place:

| Secret | Stored in |
| --- | --- |
| Tiger Cloud `tsdbadmin` password | Your password manager. Used only from your laptop for schema changes. |
| `rescrub_app` database password | Password manager, and `DATABASE_URL` in `backend/.env` on both servers |
| Object Storage access and secret keys | Password manager, and `backend/.env` on both servers |
| GitHub deploy keys | One per server, created on that server (`~/.ssh/rescrub_deploy`); never copied elsewhere |
| SSH private key | Your laptop only (`~/.ssh`), with a passphrase. Vultr gets the `.pub` line. |

`backend/.env` is mode 600 and is never committed. `web/.env.local` holds no secrets.

---

## 1. GitHub

1. `main` is what the servers deploy. Changes reach `main` only through pull requests.
2. **Settings → Branches** (or Rulesets) for `main`: require a pull request and require the
   **CI** status checks to pass.
3. **Settings → Code security**: turn on **Secret scanning** and **Push protection**.
4. CI (`.github/workflows/ci.yml`) runs on every pull request:
   - backend lint and tests against Postgres;
   - website lint, type check and build;
   - `npm audit`;
   - a gitleaks secret scan of every commit.

   Dependabot opens weekly update PRs.
5. The servers pull the repository with **deploy keys**: one read-only SSH key per server,
   valid for this repository only. Each key is created on its server in sections 5 and 7 and
   added under **Settings → Deploy keys** (see "Deploy key" below).

### Deploy key

Run on each server, as the user that will own `~/reScrub` (`rescrub` on the web server,
`root` on the GPU server):
```bash
ssh-keygen -t ed25519 -N "" -C "rescrub-$(hostname)" -f ~/.ssh/rescrub_deploy
cat >> ~/.ssh/config <<'CFG'
Host github.com
  IdentityFile ~/.ssh/rescrub_deploy
  IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config
ssh-keyscan github.com >> ~/.ssh/known_hosts
cat ~/.ssh/rescrub_deploy.pub     # one line starting with ssh-ed25519
```
In GitHub: repository **Settings → Deploy keys → Add deploy key**. Title it after the server
(`rescrub-web`, `rescrub-gpu`), paste the `.pub` line, and leave **Allow write access**
unchecked. Then check it from the server: `ssh -T git@github.com` answers
"Hi Abhik837/reScrub! You've successfully authenticated…".

GitHub accepts a key on only one repository, so each server gets its own. The private key
(`~/.ssh/rescrub_deploy`, without `.pub`) never leaves its server.

## 2. Tiger Cloud (TimescaleDB)

Tiger Cloud is Tiger Data's fully managed PostgreSQL service with TimescaleDB. reScrub uses it
for accounts, the job queue, tracking data (TimescaleDB hypertables) and findings, plus pgvector
for person embeddings.

1. In the Tiger Cloud console, **Create service**:
   - type **Time series and analytics** (TimescaleDB + pgvector);
   - region **AWS US East (N. Virginia)**, closest to Vultr New Jersey;
   - start with the smallest compute size and scale up as recordings grow.

   Save the `tsdbadmin` password in your password manager and note the host and port.
2. Create the tables and the app user from your laptop (needs `psql` 16+ and a clone of this
   repository):
   ```bash
   cd reScrub/backend
   export ADMIN_DATABASE_URL='postgres://tsdbadmin:<admin password>@<host>:<port>/tsdb?sslmode=require'
   psql "$ADMIN_DATABASE_URL" -f db/schema.sql

   APP_PW=$(openssl rand -base64 32 | tr -d '/+='); echo "$APP_PW"   # save in the password manager
   psql "$ADMIN_DATABASE_URL" -v app_password="'$APP_PW'" -f db/app_role.sql
   ```
   - `schema.sql` enables `timescaledb` and `vector`, and creates the account, job,
     tracking (hypertables with compression) and findings tables. It is safe to re-run.
   - `app_role.sql` creates `rescrub_app`, which can read and write rows but cannot drop or
     alter tables.
3. The servers connect as `rescrub_app` with certificate verification:
   ```
   DATABASE_URL=postgres://rescrub_app:<app password>@<host>:<port>/tsdb?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt
   ```
4. After sections 5 and 7, open the service's **Security → IP allow list** and allow only the
   web server IP, the GPU server IP and your own IP.
5. Test from each server once it exists: `psql "<DATABASE_URL>" -c "select 1"`.

## 3. Vultr Object Storage

1. **Products → Object Storage → Add**, location **New Jersey (ewr1)**.
2. Create a **private** bucket named `rescrub-videos`.
3. Copy the hostname (`ewr1.vultrobjects.com`), the access key and the secret key.

Videos are streamed to their owner through the API; the bucket is never public.

## 4. Domain (rescrub.health)

At the registrar's DNS settings (Porkbun → Domain Management → DNS), remove any default or
parked records, then add:

| Type | Host | Answer | TTL |
| --- | --- | --- | --- |
| A | *(blank)* | web server IPv4 | 600 |
| A | `www` | web server IPv4 | 600 |

- `www.rescrub.health` redirects to `rescrub.health`.
- The API is at `rescrub.health/api`, so it needs no record of its own.
- The GPU server needs no DNS record.
- If the web server has IPv6 enabled, add matching `AAAA` records.

Check with `dig +short rescrub.health` before section 5's HTTPS step; Caddy needs the record
in place to get a certificate.

## 5. Web server (website + API)

**Create:** Vultr → **Deploy → Cloud Compute → Shared CPU**, location **New Jersey**,
**Ubuntu 24.04 LTS**, **2 vCPU / 4 GB**, your SSH key, hostname `rescrub-web`. Put its IP in
DNS (section 4).

**Base setup** (`ssh root@WEB_IP`):
```bash
apt update && apt -y upgrade
curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
apt install -y nodejs git caddy ufw docker.io docker-compose-v2 unattended-upgrades postgresql-client
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
npm install -g pm2

# The app runs as an unprivileged user that may use Docker
adduser --disabled-password --gecos "" rescrub && usermod -aG docker rescrub
mkdir -p /home/rescrub/.ssh && cp ~/.ssh/authorized_keys /home/rescrub/.ssh/ && chown -R rescrub:rescrub /home/rescrub/.ssh

# SSH keys only. Confirm `ssh rescrub@WEB_IP` works in a second window first.
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config && systemctl restart ssh
```

**API and website** (as `rescrub`: `ssh rescrub@WEB_IP`):
```bash
# First: create this server's deploy key and add it to GitHub (section 1, "Deploy key")
git clone git@github.com:Abhik837/reScrub.git ~/reScrub

# API
cd ~/reScrub/backend
cp .env.example .env && chmod 600 .env
nano .env        # DATABASE_URL (section 2), S3_ACCESS_KEY / S3_SECRET_KEY (section 3),
                 # optional GEMINI_API_KEY for final reports (from https://aistudio.google.com)
psql "$(grep ^DATABASE_URL .env | cut -d= -f2-)" -c "select 1"   # database reachable
docker compose --profile api up -d --build
curl -s localhost:8000/health    # {"ok":true}

# Website
cd ~/reScrub/web
printf 'NEXT_PUBLIC_API_BASE_URL=/api\nAPI_INTERNAL_URL=http://127.0.0.1:8000\n' > .env.local
npm ci && npm run build
pm2 start npm --name rescrub-web -- start -- -H 127.0.0.1
pm2 save && pm2 startup          # run the command it prints, as root
```

**HTTPS and routing** (as root):
```bash
cp /home/rescrub/reScrub/deploy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy
```
Caddy obtains and renews the certificates automatically. The Caddyfile:
- sends `/api/*` to the API, with a 520 MB upload limit and streaming;
- sends everything else to the website;
- redirects `www` to the bare domain.

## 6. Accounts

Accounts are invite-only. Create them on the web server, as `rescrub`:
```bash
cd ~/reScrub/backend
docker compose --profile api run --rm api python -m rescrub.accounts add you@school.edu --admin
docker compose --profile api run --rm api python -m rescrub.accounts add trainee@school.edu
```
Each command prints a generated 20-character password. Send it to the person through a
private channel; they sign in at `https://rescrub.health/login`.

| Task | Command (same `docker compose … run --rm api` prefix) |
| --- | --- |
| New password (signs them out everywhere) | `python -m rescrub.accounts reset <email>` |
| Block or unblock | `python -m rescrub.accounts disable <email>` / `enable <email>` |
| List accounts | `python -m rescrub.accounts list` |
| Delete an account with all its recordings and files | `python -m rescrub.accounts delete <email>` |

Users delete their own recordings with **Delete recording** in the review.

## 7. GPU server (analysis)

**Create:** Vultr → **Deploy → Cloud GPU**, location **New Jersey** (or the nearest with
stock), **Ubuntu 22.04 LTS** with NVIDIA drivers, your SSH key, hostname `rescrub-gpu`.
- Analysis alone needs about 16 GB of GPU memory.
- Training is much faster on an **NVIDIA L40S 48 GB** or **A100**. Use one size for both, or
  train on a larger instance and then move to a smaller one for analysis.

Attach **Block Storage** in the same location for datasets and training runs: about 100 GB
for the `tiny` training profile, 150 GB for `small`, 1 TB for `full`.

```bash
ssh root@GPU_IP
apt update && apt -y upgrade
apt install -y docker.io docker-compose-v2 git ufw unattended-upgrades postgresql-client
ufw allow 22 && ufw --force enable          # nothing else inbound: the worker pulls jobs
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl restart ssh
nvidia-smi                                  # shows the GPU; if not: ubuntu-drivers install && reboot

# NVIDIA Container Toolkit (lets Docker use the GPU)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt update && apt install -y nvidia-container-toolkit && nvidia-ctk runtime configure --runtime=docker && systemctl restart docker

# Block storage. Check the device name with lsblk; mkfs erases it, so run it only on a new volume.
lsblk
mkfs.ext4 /dev/vdb && mkdir -p /mnt/data && mount /dev/vdb /mnt/data
echo '/dev/vdb /mnt/data ext4 defaults,nofail 0 2' >> /etc/fstab

# First: create this server's deploy key and add it to GitHub (section 1, "Deploy key")
git clone git@github.com:Abhik837/reScrub.git ~/reScrub
cd ~/reScrub/backend
cp .env.example .env && chmod 600 .env && nano .env      # same DATABASE_URL and S3_* as the web server
psql "$(grep ^DATABASE_URL .env | cut -d= -f2-)" -c "select 1"
mkdir -p models/trained && chown -R 1000:1000 models /mnt/data
docker compose --profile worker build                    # includes the base model weights (~15 min)
docker compose --profile worker up -d
docker compose --profile worker logs -f worker           # "Worker ready"
```

The worker now analyzes uploads with the base models and restarts on reboot
(`restart: unless-stopped`). Section 8 replaces the base models with ones trained for the OR.

## 8. Training (MM-OR)

Training uses the [MM-OR](https://github.com/egeozsoy/MM-OR) dataset. Accept its terms
first; the download comes from `www.campar.in.tum.de`. Details on what is trained, the
train/validation/test separation and the scores are in
[backend/training/README.md](../backend/training/README.md).

On the GPU server, inside `tmux` so the run survives a dropped SSH connection
(`apt install -y tmux`, then `tmux new -s train`; reattach with `tmux attach -t train`):
```bash
cd ~/reScrub/backend
docker compose --profile worker stop worker              # free the GPU while training
docker compose --profile worker run --rm -e DATA=/data -e PROFILE=small worker bash training/run_all.sh
```
The run:
1. downloads MM-OR to `/mnt/data` (resumes if interrupted);
2. builds the segmentation, pose and ReID datasets from the training and validation surgeries;
3. trains the three models and installs them in `models/trained/`;
4. runs the full pipeline on the held-out test surgeries and scores the findings against
   MM-OR's labeled sterility breaches.

| Profile | Surgeries (train / val / test) | Download (zipped) |
| --- | --- | --- |
| `tiny` | 1 / 1 / 1 | ~28 GB (7.6 GB without the test surgery) |
| `small` | 2 / 1 / 1 | ~41 GB |
| `full` | 9 / 3 / 4 | ~432 GB |

**Shorter runs.** Pass any of these with `-e` (details in the training README, "Shorter runs"):
- `PROFILE=tiny`: one surgery per split.
- `EVAL_SPLIT=val`: scores on the validation surgery and skips the test download.
- `EVERY=6`: uses every 6th labeled frame.
- `IMGSZ=640`: trains at 640 px.
- `SEG_EPOCHS=30`, `POSE_EPOCHS=15`, `REID_EPOCHS=10`: fewer training epochs.
- `MODELS=seg`: trains only the surface and role model.

The quickest useful run:
```bash
docker compose --profile worker run --rm -e DATA=/data -e PROFILE=tiny -e EVAL_SPLIT=val -e MODELS=seg \
  -e EVERY=6 -e IMGSZ=640 -e SEG_EPOCHS=30 worker bash training/run_all.sh
```

Then switch the worker to the models you trained. In `backend/.env`, set:
```
SURFACE_CONFIG=config/surfaces_mmor.yaml       # seg
POSE_MODEL=trained/rescrub-or-pose.pt          # pose (skip if MODELS didn't include it)
EMBED_MODEL=trained/rescrub-reid-cls.pt        # reid (skip if MODELS didn't include it)
```
and restart: `docker compose --profile worker up -d`. The scores are in
`/mnt/data/eval_results/<test or val>/evaluation.json`, with labeled videos beside them.

## 9. Verify

**End to end:**
1. `https://rescrub.health` loads; `https://rescrub.health/api/health` returns `{"ok":true}`.
2. `https://rescrub.health/review` sends you to **Sign in**. Sign in with an account from section 6.
3. Upload a short MP4. It goes from queued to analyzing to completed and shows the labeled
   video with timestamped findings. Seeking works.
4. Confirm a finding and reload: the decision is kept. **Sign out**; `/review` asks you to
   sign in again.

**Security** (from your laptop):
```bash
curl -sI https://rescrub.health | grep -iE "content-security|strict-transport"       # both present
curl -s -o /dev/null -w "%{http_code}\n" https://rescrub.health/api/docs                # 404
curl -s https://rescrub.health/api/sessions/00000000-0000-0000-0000-000000000000        # {"error":"Please sign in."}
curl -s -X POST -H "Origin: https://evil.example" -H "Content-Type: application/json" \
  -d '{"email":"a@b.co","password":"x"}' https://rescrub.health/api/auth/login          # 403
nc -zv -w3 WEB_IP 3000; nc -zv -w3 WEB_IP 8000; nc -zv -w3 GPU_IP 8000                  # all refused
for i in $(seq 12); do curl -s -o /dev/null -w "%{http_code} " -H "Origin: https://rescrub.health" \
  -H "Content-Type: application/json" -d '{"email":"nobody@example.com","password":"wrong-password"}' \
  https://rescrub.health/api/auth/login; done; echo                                      # 401 ×10, then 429
```
On both servers, `ls -l ~/reScrub/backend/.env` shows `-rw-------`.

**Troubleshooting:**

| Problem | Where to look |
| --- | --- |
| Uploads stay "queued" | GPU server: `docker compose --profile worker logs worker`. Is it running, and does `psql` reach Tiger Cloud (IP allow list)? |
| Sign-in fails for everyone | Web server: `docker compose --profile api logs api`. `CORS_ORIGINS` must be exactly `https://rescrub.health`. |
| `/review` returns to sign-in after signing in | `web/.env.local` needs `API_INTERNAL_URL=http://127.0.0.1:8000`; rebuild and `pm2 restart rescrub-web`. |
| Database certificate error | `DATABASE_URL` must end in `sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt`. The app's Postgres driver bundles its own OpenSSL, which doesn't find the system certificates from `sslrootcert=system`. |
| No HTTPS | DNS doesn't point at the web server yet; `journalctl -u caddy`. |

## 10. Operations

**Deploy a change** (after its pull request is merged into `main`):
- Web server, as `rescrub`: `bash ~/reScrub/deploy/update-web.sh` (pulls, rebuilds the API
  and website, restarts both, checks `/api/health`).
- GPU server: `bash ~/reScrub/deploy/update-worker.sh`.
- If the change touches `backend/db/schema.sql`, apply it first from your laptop with the
  admin URL: `psql "$ADMIN_DATABASE_URL" -f db/schema.sql`. A new table also needs the app
  user's permissions, e.g. for the `reports` table:
  `psql "$ADMIN_DATABASE_URL" -c "GRANT SELECT, INSERT, UPDATE, DELETE ON reports TO rescrub_app"`.

**Logs:** `docker compose --profile api logs -f api`, `pm2 logs rescrub-web`,
`journalctl -u caddy` (web server); `docker compose --profile worker logs -f worker` (GPU server).

**Backups:** Tiger Cloud backs up the service automatically; restores are in the service's
**Operations** tab.

**Retrain** after adding data: repeat section 8; the worker picks up new weights on restart.

**Rotate a credential** (scheduled, or immediately if one is ever exposed):
- **Database:** `ALTER ROLE rescrub_app PASSWORD '<new>';` as `tsdbadmin`, then update
  `DATABASE_URL` on both servers and restart the API and worker. Reset `tsdbadmin` in the
  Tiger Cloud console.
- **Object Storage:** regenerate the keys in Vultr, update `S3_*` on both servers, restart.
- **Gemini:** create a new key in Google AI Studio, update `GEMINI_API_KEY` on the web server,
  restart the API, then delete the old key.
- **SSH:** add the new public key to `~/.ssh/authorized_keys` on both servers and in Vultr,
  confirm it works, then remove the old one.
- **Deploy key:** on the server, delete `~/.ssh/rescrub_deploy*` and create a new key as in
  section 1; in GitHub, add the new key and delete the old one under **Settings → Deploy keys**.
