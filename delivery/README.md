# delivery — the local kiosk delivery server

A small stdlib-only web server that bridges the offline pipeline and the
kiosk browser. The pipeline (`classify -> fill -> render -> deliver`) runs on
the agent worker and writes `portrait.png` / `qr.png` / `status.json` into
`sessions/<session-id>/`. This server serves those files and exposes a
pipeline-progress status endpoint the kiosk polls for its stage-by-stage
reveal.

## Run it

```sh
uv run python -m delivery.server
```

It binds `0.0.0.0` on `DELIVERY_SERVER_PORT` (default `8808`) and serves out
of `SESSIONS_DIR` (default `./sessions`). Both are read from the environment
/ `.env`.

## What it serves

| Route | Response |
|---|---|
| `GET /<session-id>/portrait.png` | the rendered portrait PNG |
| `GET /<session-id>/qr.png` | the QR-code PNG |
| `GET /<session-id>/` | a minimal mobile page showing the portrait full-bleed, with a download button — **this is the page the QR points a phone at** |
| `GET /<session-id>/<file>.json` | a per-session JSON artifact (below) |
| `GET /status/<session-id>` | pipeline-progress JSON (below) |
| `GET /live/<session-id>` | live interview-state JSON, polled during an interview |
| `GET /sessions` | the index of *every* session folder on this machine (below) |

The status endpoint returns:

```json
{
  "session_id": "...",
  "stage": "pending|classifying|filling|rendering|delivering|done|error",
  "portrait_url": "/<session-id>/portrait.png  or null",
  "qr_url": "/<session-id>/qr.png  or null",
  "error": "<message>  or null"
}
```

`stage` is read from `sessions/<session-id>/status.json`, which
`agent.session_finalize.run_offline_pipeline` rewrites at each stage boundary.
A session folder (or `status.json`) that does not exist yet degrades to stage
`pending` — never a 500. The kiosk polls `/status/<session-id>` while the
pipeline runs and switches to the portrait + QR once `stage` is `done`.

## The sessions index — `GET /sessions`

The dev dashboard's session list. It enumerates every session folder under
`SESSIONS_DIR` and returns one summary per interview — active and past:

```json
{
  "sessions": [
    {
      "session_id": "<folder name>",
      "phase": "pending|base_questions|probing|complete|unknown",
      "pipeline_stage": "pending|classifying|filling|rendering|delivering|done|error  or null",
      "turn_count": 0,
      "chosen_template": "iceberg|two_buttons|compass|arc  or null",
      "has_transcript": true,
      "has_portrait": false,
      "has_classification": false,
      "updated_at": "<ISO-8601 timestamp>",
      "portrait_url": "/<session-id>/portrait.png  or null"
    }
  ]
}
```

Each field is read from whatever JSON files the folder happens to hold —
`live_state.json` (`phase`, `turn_count`, `chosen_template`, `updated_at`),
`status.json` (`pipeline_stage`), `interview_state.json` (`chosen_template`
fallback), `transcript.json` (`turn_count` fallback). A folder with only a
partial set of files is summarised gracefully — never a 500. `phase` is
`unknown` (not `pending`) when there is no `live_state.json` at all;
`pipeline_stage` is `null` when there is no `status.json`. Summaries are
sorted most-recent-first by `updated_at`; a session with no `live_state.json`
falls back to the folder's most-recent file mtime. A missing or empty
`SESSIONS_DIR` returns `{"sessions": []}`, not an error.

## Per-session JSON artifacts — `GET /<session-id>/<file>.json`

So a dev detail view can fetch a past interview's transcript, the server
serves the known JSON artifacts directly out of a session folder:
`transcript.json`, `interview_state.json`, `classification.json`,
`result.json`, `live_state.json`, `status.json`. The filename is checked
against a fixed allowlist — only those six names are served, so a `../`
path-traversal attempt can never reach outside the session folder. A request
for a file the session does not have yet 404s gracefully.

## It must run on the kiosk machine

The server runs on the **kiosk machine** and must be reachable by phones on
the **same wifi** — that is how a scanned QR code reaches the portrait.

> **Important:** `DELIVERY_SERVER_URL` (used by `pipeline/deliver.py` to build
> the QR payload) must be set to the **kiosk machine's LAN IP**, e.g.
> `http://192.168.1.42:8808`. `localhost` only resolves on the kiosk itself —
> a phone scanning a `localhost` QR will fail. Find the kiosk's LAN IP with
> `ipconfig getifaddr en0` (macOS) or `hostname -I` (Linux).

For local testing on the kiosk itself, the `http://localhost:8808` default is
fine.
