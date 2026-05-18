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
| `GET /status/<session-id>` | pipeline-progress JSON (below) |

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
