# WhatsApp Bridge (Node Sidecar)

Minimal HTTP bridge between WhatsApp Web and the agent.

## What it does

- Maintains a WhatsApp Web session (QR or pairing code on first run).
- For inbound messages: POSTs `{from, body}` to the agent webhook.
- For outbound messages: accepts `POST /send` from the agent.
- Health check: `GET /health` for admin UI test.

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

`GET http://<bridge-host>:3001/health` -> `{ "ok": true }`

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

## Notes

- This uses WhatsApp Web automation and may violate WhatsApp terms.
- For production, prefer Twilio WhatsApp or Meta Cloud API if you need official support.
