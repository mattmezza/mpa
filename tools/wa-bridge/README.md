# WhatsApp Bridge (Node Sidecar)

Minimal HTTP bridge between WhatsApp Web and the agent.

## What it does

- Maintains a WhatsApp Web session (QR or pairing code on first run).
- For inbound messages: POSTs `{from, body}` to the agent webhook.
- For outbound messages: accepts `POST /send` from the agent.
- Health check: `GET /health` for admin UI test.
- Auth status + QR: `GET /auth/status` + `GET /auth/qr` for admin UI setup.
- Start/stop session: `POST /auth/start` and `POST /auth/stop`.
- Restart session: `POST /auth/restart`.
- Logout: `POST /auth/logout` to clear auth data.

## API contract

Inbound (bridge -> agent):

`POST http://<agent-host>:8000/webhook/whatsapp`

```json
{
  "from": "+393331234567",
  "body": "Hello"
}
```

Outbound (agent -> bridge):

`POST http://<bridge-host>:3001/send`

```json
{
  "to": "+393331234567",
  "text": "Hi from the agent"
}
```

Health:

`GET http://<bridge-host>:3001/health` -> `{ "ok": true, "authenticated": bool, "ready": bool }`

Auth status:

`GET http://<bridge-host>:3001/auth/status` -> `{ "ok": true, "started": bool, "authenticated": bool, "ready": bool, "has_qr": bool, "client": {"wid": "", "pushname": ""} }`

Start session:

`POST http://<bridge-host>:3001/auth/start` -> `{ "ok": true, "started": true }`

Stop session:

`POST http://<bridge-host>:3001/auth/stop` -> `{ "ok": true, "started": false }`

Restart session:

`POST http://<bridge-host>:3001/auth/restart` -> `{ "ok": true, "started": true }`

Auth QR:

`GET http://<bridge-host>:3001/auth/qr` -> `{ "ok": true, "qr": "...", "data_url": "data:image/png;base64,..." }`

Logout:

`POST http://<bridge-host>:3001/auth/logout` -> `{ "ok": true }`

## Auth (recommended)

Use a shared token header to prevent random callers:

- Bridge checks `X-WA-Bridge-Token` on inbound `POST /send`.
- Agent webhook checks the same header on `POST /webhook/whatsapp`.

If you only expose the bridge on localhost/VPC, this can be optional.

## Setup

1. Install deps:

```bash
cd tools/wa-bridge
npm install
```

2. Configure env:

```bash
cp .env.example .env
```

3. Start:

```bash
npm run start
```

You will be prompted with a QR code or a pairing code on first run.

## Auth persistence

- Auth state is stored under `AUTH_PATH` (default `.wwebjs_auth`).
- If you run in Docker, mount a persistent volume to that path.

## Bridge lifecycle

By default the bridge starts in idle mode and waits for `/auth/start`.
Set `AUTO_START=true` if you want it to initialize the WhatsApp session on boot.

## CORS

If you use the admin UI from another origin, set `CORS_ORIGIN` to allow that host.
Example: `CORS_ORIGIN=http://localhost:8000` or a comma-separated list.

## Notes

- This uses WhatsApp Web automation and may violate WhatsApp terms.
- For production, prefer Twilio WhatsApp or Meta Cloud API if you need official support.
