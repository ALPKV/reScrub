# reScrub website

Next.js (App Router), TypeScript and Tailwind CSS. Sign-in, upload, and the review workspace:
labeled video, timeline, findings with evidence chains, Confirm/Dismiss, export and delete.

## Run it

```bash
npm ci
npm run dev
```

Open http://localhost:3000. Without a backend, the demonstration (`/review?demo=1`) works and
the rest of the site shows that analysis isn't connected. To connect a local backend, see the
Development section of the [root README](../README.md).

Settings (`.env.local`, see `.env.example`):

| Variable | Production value | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `/api` | Where the browser reaches the API (same origin). Public; baked in at build time. |
| `API_INTERNAL_URL` | `http://127.0.0.1:8000` | Server-side only: how the website checks sign-ins before showing `/review`. |
| `API_DEV_PROXY` | – | Development only: forward `/api` to a local backend. |

## Where things are

| Path | What it does |
| --- | --- |
| `app/page.tsx` | Homepage (`/`) |
| `app/login/page.tsx`, `components/SignInForm.tsx` | Sign-in (`/login?next=…`) |
| `app/review/page.tsx` | Review workspace (`/review`, sign-in required; checks the session on the server). `/review?demo=1` is the public demonstration |
| `proxy.ts` | Sends visitors without a sign-in cookie from `/review` to `/login` |
| `app/terms`, `app/privacy` | Terms and Privacy pages |
| `app/icon.png`, `public/brand/` | Logo and favicon ([docs/ASSETS.md](docs/ASSETS.md)) |
| `components/AppHeader.tsx`, `AccountNav.tsx` | Header with the signed-in account and Sign out |
| `components/ReviewWorkspace.tsx` | Workspace state: setup → upload → analysis → review |
| `components/VideoUpload.tsx` | Drag-and-drop / file picker, preview, replace/remove |
| `components/SessionSetup.tsx`, `SetupStep.tsx` | Numbered steps: recording, context, permission, analyze |
| `components/AnalysisStatus.tsx` | Uploading / queued / analyzing / failed / canceled panel |
| `components/VideoPlayer.tsx` | Player with keyboard controls; plays the labeled video when available |
| `components/EventTimeline.tsx` | Scrubber with numbered finding markers |
| `components/FindingsPanel.tsx`, `FindingDetail.tsx` | Filters, finding cards, Confirm/Dismiss/Undo |
| `components/ContactChain.tsx` | Evidence chain (e.g. side table → hand → instrument table) |
| `components/ReviewSummary.tsx` | Session summary, Export review, Delete recording |
| `lib/api.ts` | The only module that calls the analysis API |
| `lib/auth.ts`, `lib/session.ts` | Sign-in, sign-out, current account (browser); session check (server) |
| `lib/validate.ts` | Validation of every API response |
| `lib/types.ts` | Data shapes shared with the API ([docs/API.md](../docs/API.md)) |
| `lib/demo.ts`, `scripts/make_demo_video.py` | Demonstration findings and the synthetic clip they describe |

## Rules

- Demo findings are only shown with the synthetic demo clip, never on an uploaded video.
- No secrets in this folder: `NEXT_PUBLIC_API_BASE_URL` is public and `API_INTERNAL_URL` is an
  internal address.
- Checks in the browser are conveniences; the API enforces every rule ([docs/SECURITY.md](../docs/SECURITY.md)).

## Troubleshooting

**Styles look out of date after editing `app/globals.css`.** Stop the dev server, delete
`.next/cache/turbopack`, and start it again.
