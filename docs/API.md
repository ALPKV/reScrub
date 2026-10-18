# API reference

The API is served at **`https://rescrub.health/api`**, on the same origin as the website.
The website reaches it only through `web/lib/api.ts` and `web/lib/auth.ts`, and the shapes
it accepts are in `web/lib/types.ts` and are validated by `web/lib/validate.ts`.

## Conventions

- All responses are JSON. Errors use an HTTP error status and `{ "error": "Human-readable message" }`.
  The website shows that message.
- **Sign-in:** every `/sessions` request needs the session cookie (`401 {"error":"Please sign in."}`
  otherwise) and only reaches recordings owned by the signed-in account (`404` otherwise,
  the same as for an ID that doesn't exist).
- **Origin:** every `POST`, `PATCH` and `DELETE` must carry `Origin: https://rescrub.health`
  (a value in `CORS_ORIGINS`), or it is refused with `403`.
- **IDs:** `sessionId` is a UUID; `eventId` matches `^[A-Za-z0-9_-]{1,128}$`.
- **Rate limits** (per client): 10 uploads per hour; 120 requests per minute on session
  endpoints; sign-in 10 attempts per email and 60 per IP per 15 minutes. Over the limit: `429`.

## Sign-in

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/auth/login` | `{ "email", "password" }` | `{ "email", "role" }` and sets the session cookie. Wrong email or password: `401 {"error":"Email or password is incorrect."}` |
| `POST` | `/auth/logout` | – | `{}`. Ends the session on the server and clears the cookie |
| `GET` | `/auth/me` | – | `{ "email", "role" }` (`role` is `member` or `admin`), or `401` |

## Recordings

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/sessions` | `multipart/form-data`: `video` (MP4, ≤ 500 MB, ≤ 60 min), `name` (≤ 120), `procedureContext` (optional, ≤ 2000), `acknowledged` (`"true"`), `zones` (optional JSON, see below) | `{ "sessionId" }` |
| `GET` | `/sessions/{sessionId}` | – | `JobStatus` |
| `POST` | `/sessions/{sessionId}/cancel` | – | `{}` |
| `PATCH` | `/sessions/{sessionId}/findings/{eventId}` | `{ "reviewStatus": "needs_review" \| "confirmed" \| "dismissed" }` | `{}` |
| `DELETE` | `/sessions/{sessionId}` | – | `{}`. Deletes the recording, labeled video, tracking data and findings |
| `GET` | `/sessions/{sessionId}/annotated.mp4` | – | The labeled video (H.264), with HTTP Range support for seeking |
| `GET` | `/sessions/{sessionId}/report` | – | `Report`, or 404 if none has been written |
| `POST` | `/sessions/{sessionId}/report` | – | `Report`. Asks Google Gemini to write a final report (see below) |

The website polls `GET /sessions/{id}` every 3 seconds while the job is `queued` or `analyzing`.

### Final report (`Report`)

`POST /sessions/{id}/report` sends **only text** to Google Gemini: each finding's title,
description, reason, evidence labels and times, its uncertainty, the reviewer's decision, summary
counts, the recording length and the optional procedure context. The video, frames, file name,
session name, account details and tracking data are never sent. The report replaces any earlier
one and is deleted with the recording.

Errors: `409` if the analysis isn't finished, `503` if `GEMINI_API_KEY` isn't set (or Gemini is
busy), `502`/`504` if Gemini fails or its answer doesn't pass validation, `429` after
`REPORTS_PER_HOUR_PER_USER` reports in an hour.

```json
{
  "sessionId": "…",
  "generatedAt": "2026-10-18T15:04:05+00:00",
  "model": "gemini-flash-latest",
  "stale": false,
  "stats": { "findings": 3, "confirmed": 1, "needsReview": 1, "dismissed": 1, "highUncertainty": 1 },
  "overview": "Plain-text summary of the session.",
  "keyMoments": [
    { "eventId": "evt-1", "timestampSec": 12.0, "title": "…", "reviewStatus": "confirmed", "uncertainty": "low",
      "summary": "What happened.", "teachingPoint": "What to practice." }
  ],
  "coachingPoints": ["…"],
  "limitations": "What this review can't show."
}
```

Gemini's answer is untrusted: it must match a JSON schema; `keyMoments` may only name existing,
non-dismissed findings; `timestampSec`, `title`, `reviewStatus`, `uncertainty` and `stats` come from
the database, never from the model; all text is plain and length-capped. `stale` is `true` when a
reviewer decision changed after the report was written.

### `JobStatus`

```json
{
  "sessionId": "6f1c…",
  "state": "queued | analyzing | completed | failed | canceled",
  "progress": 0.42,
  "error": "only when failed",
  "result": {
    "sessionId": "6f1c…",
    "source": "backend",
    "createdAt": "2026-10-18T18:00:00+00:00",
    "annotatedVideoUrl": "/api/sessions/6f1c…/annotated.mp4",
    "findings": [
      {
        "sessionId": "6f1c…",
        "eventId": "evt-1",
        "timestampSec": 10.5,
        "endSec": 16,
        "title": "Person 2's right hand touches Side table, then Instrument table",
        "objects": ["Side table", "Person 2 right hand", "Instrument table"],
        "description": "What was seen, written neutrally.",
        "reasonForReview": "Why a person should look at this.",
        "evidence": [
          { "label": "Side table", "designation": "nonsterile", "timestampSec": 11, "observation": "…" },
          { "label": "Instrument table", "designation": "sterile", "timestampSec": 15.5, "observation": "…" }
        ],
        "uncertainty": { "level": "low | medium | high", "notes": "Visibility limits." },
        "reviewStatus": "needs_review"
      }
    ]
  }
}
```

- `result` is present only when `state` is `completed`. `findings` has at most 200 items and
  each `evidence` list at most 12 steps. All times are seconds from the start of the recording.
- `annotatedVideoUrl` is a path on this origin. The website accepts it only when it is exactly
  `/api/sessions/<sessionId>/annotated.mp4`, and then plays it instead of the local copy.
- `designation` comes from the room's surface configuration: the trained model's classes
  (`backend/config/surfaces_mmor.yaml`) or hand-drawn zones. It is `"unknown"` for anything
  that has no designation. `uncertainty.level` is `high` when contact is judged from image
  position only (no depth).

### Hand-drawn zones (`zones`)

Optional. Used instead of automatic surface detection for that recording; a server-wide
default can be set with `DEFAULT_ZONES_FILE`. Coordinates are pixels or fractions of the
frame, and earlier zones win where they overlap.

```json
[
  {"label": "Sterile drape", "designation": "sterile",
   "polygon": [[0.35, 0.45], [0.65, 0.45], [0.65, 0.8], [0.35, 0.8]]},
  {"label": "Side table", "designation": "nonsterile",
   "polygon": [[0, 0.8], [1, 0.8], [1, 1], [0, 1]]}
]
```

`designation` is one of `sterile`, `nonsterile`, `patient`, `neutral`.

## Tracking data

For analysis tools and models. Same sign-in and ownership rules apply.

| Method | Path | Query | Returns |
| --- | --- | --- | --- |
| `GET` | `/sessions/{id}/people` | – | `Person N` labels, first/last seen, frames, designation (scrubbed = `sterile`) |
| `GET` | `/sessions/{id}/tracks` | `person`, `from`, `to`, `parts` (e.g. `r_wrist,r_index_tip`), `limit` ≤ 50,000 | Keypoints per frame: `video_sec`, `frame`, `person_label`, `part`, `x`, `y`, `conf`, `depth` |
| `GET` | `/sessions/{id}/surfaces` | – | Surface polygons per keyframe, with `designation` |
| `GET` | `/sessions/{id}/proximity` | `candidate_only`, `from`, `to`, `limit` ≤ 50,000 | Hand/arm-to-surface features: `pixel_dist`, `inside_mask`, `depth_gap`, `speed_px_s`, `candidate_contact` |

## Health

`GET /health` returns `{"ok": true}` when the API can reach the database.
