# Security

How reScrub protects accounts, recordings and the servers, and how to check it.

## Accounts and sign-in

| Control | Implementation |
| --- | --- |
| Invite-only accounts | Created by an administrator with `python -m rescrub.accounts` (see `docs/DEPLOY.md`). No public sign-up. |
| Password storage | Argon2id (`argon2-cffi`), re-hashed automatically when parameters change. Minimum 12 characters; generated passwords are 20 characters. |
| Login errors | One message for every failure ("Email or password is incorrect."). An unknown email takes as long as a wrong password. |
| Brute force | 10 attempts per email and 60 per IP in 15 minutes, then `429`. |
| Session cookie | `__Host-rescrub_session`: random 256-bit value, HttpOnly, Secure, SameSite=Lax, Path=/, no Domain, 7 days. Only its SHA-256 is stored (`auth_sessions`). |
| Ending sessions | Sign-out deletes the session on the server. A password reset or disabling an account ends all of that user's sessions immediately. |
| CSRF | SameSite=Lax, plus every POST/PATCH/DELETE must carry an `Origin` listed in `CORS_ORIGINS`. |
| Page protection | `web/proxy.ts` redirects requests without a cookie to `/login`; `web/app/review/page.tsx` checks the session on the server through `API_INTERNAL_URL`; the API checks it again on every request. |
| Return after sign-in | Only same-site paths are accepted (`safeNextPath`), so `?next=` can't redirect elsewhere. |

## Recordings and data

| Control | Implementation |
| --- | --- |
| Ownership | Every `/sessions/...` request checks that the signed-in account owns the recording; anything else returns `404`, the same as a missing ID. |
| Upload checks | Size (`Content-Length` and a running count while streaming, plus Caddy's 520 MB limit), the MP4 `ftyp` marker, and `ffprobe` (video stream, duration ≤ 60 min). Text fields: name ≤ 120, context ≤ 2000, acknowledgment required. |
| Storage | Files are named by random UUIDs; the uploaded filename is never used for a path. Private Vultr Object Storage bucket. |
| Retention | The original upload is deleted once the labeled video is made (`DELETE_ORIGINAL_AFTER_PROCESSING=true`). Users delete a recording and everything derived from it with **Delete recording**; administrators can delete an account and all its data. |
| Video delivery | The labeled video is streamed by the API to its owner only. No storage links or static files are ever exposed. The website accepts the video only from `/api/sessions/<id>/annotated.mp4`. |
| Database access | The servers use `rescrub_app` (`backend/db/app_role.sql`), which can read and write rows but cannot drop or alter tables. All queries are parameterized. TLS with certificate verification (`sslmode=verify-full`). |
| Output limits | ≤ 200 findings per recording, ≤ 12 evidence steps each; timestamps inside the video. |
| Final report (Gemini) | `backend/rescrub/report.py`. Only finding text, reviewer decisions, counts, recording length and the optional procedure context go to Google Gemini; never video, frames, file names, session names, accounts or tracking data. The procedure context is marked as background in the prompt (prompt injection). The answer must match a JSON schema; moments must name existing, non-dismissed findings; numbers and times come from the database; text is plain and length-capped. Owner-only, same-site POST, rate-limited per account (`REPORTS_PER_HOUR_PER_USER`), 60 s timeout. Logs record sizes and timing, never the prompt or the answer. The key lives in `backend/.env` on the web server only. |

## Website

| Control | Implementation |
| --- | --- |
| No secrets in browser code | The only public setting is `NEXT_PUBLIC_API_BASE_URL=/api`. The production bundle ships no source maps. |
| Response validation | `web/lib/validate.ts` checks every API response: known states, safe IDs, timestamps 0–6 h, text length caps, list caps, unknown designations shown as "Unclear", missing uncertainty treated as high. Reports (`parseReport`) are checked the same way, and each moment must match a finding on the page. |
| No HTML from data | All text renders as plain text through React (no `dangerouslySetInnerHTML`). Control and text-direction characters are stripped. |
| Security headers | `web/next.config.ts`: Content-Security-Policy (`default-src 'self'`; scripts, styles, media and connections limited to this site), HSTS, `X-Frame-Options: DENY`, nosniff, Referrer-Policy, Permissions-Policy, Cross-Origin-Opener-Policy. CSP keeps `'unsafe-inline'` for Next.js's inline scripts. |

## Servers

| Control | Implementation |
| --- | --- |
| Network | Web server: only 22, 80 and 443 are open; the API (127.0.0.1:8000) and the website (127.0.0.1:3000) are reachable only through Caddy. GPU server: only 22. The worker pulls jobs from the database and needs nothing inbound. |
| HTTPS | Caddy with automatic certificates; `www` redirects to `rescrub.health`. |
| Client addresses | Caddy replaces any client-sent `X-Forwarded-For`, so rate limits can't be bypassed with forged headers. |
| API surface | Interactive API docs are disabled (`ENABLE_API_DOCS=false`); no `Server` header. |
| Processes | The API and worker containers run as an unprivileged user (uid 1000); the website runs under the `rescrub` user; SSH accepts keys only; unattended security upgrades are on. |
| Secrets | `backend/.env` (mode 600) on each server holds `DATABASE_URL`, the storage keys and (web server only) `GEMINI_API_KEY`; `web/.env.local` holds only public or internal addresses. `.gitignore` covers `.env*`, `*credentials*`, `*.pem`, `*.key`. |

## Supply chain

- CI on every pull request (`.github/workflows/ci.yml`):
  - backend lint and tests against Postgres;
  - website lint, type check and build;
  - `npm audit` (high severity and above);
  - a gitleaks scan of every commit on every branch.
- Dependabot opens weekly update PRs for npm, pip, Docker base images and GitHub Actions.
- GitHub secret scanning with push protection is enabled on the repository.

## Verify a deployment

Run the checks in `docs/DEPLOY.md`, section "Verify". They cover:
- the security headers;
- that the API docs are hidden;
- that requests without sign-in are refused;
- that requests from other sites are refused;
- that ports 3000 and 8000 are closed from outside;
- the sign-in rate limit.

## Operating rules

- Never paste passwords, `.env` files, database URLs or private SSH keys into chats, tickets
  or documents. If one is exposed, rotate it immediately:
  - **Tiger Cloud:** reset the password, then update `DATABASE_URL` on both servers.
  - **Object Storage:** regenerate the keys.
  - **SSH:** replace the key in Vultr and in `~/.ssh/authorized_keys` on both servers.
- Give each person their own account. Disable accounts that are no longer needed:
  `python -m rescrub.accounts disable <email>`.
