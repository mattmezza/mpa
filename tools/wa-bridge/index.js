import express from "express";
import qrcodeTerminal from "qrcode-terminal";
import qrcode from "qrcode";
import dotenv from "dotenv";
import whatsappWeb from "whatsapp-web.js";
import fs from "fs/promises";
import path from "path";

dotenv.config();

const PORT = Number(process.env.PORT || 3001);
const AGENT_WEBHOOK = process.env.AGENT_WEBHOOK || "http://localhost:8000/webhook/whatsapp";
const BRIDGE_TOKEN = process.env.BRIDGE_TOKEN || "";
const AUTH_PATH = process.env.AUTH_PATH || ".wwebjs_auth";
const CACHE_PATH = process.env.CACHE_PATH || ".wwebjs_cache";
const CLIENT_ID = process.env.CLIENT_ID || "mpa";
const AUTO_START = (process.env.AUTO_START || "").toLowerCase() === "true";

const app = express();
app.use(express.json());

const { Client, LocalAuth } = whatsappWeb;

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: AUTH_PATH, clientId: CLIENT_ID }),
  puppeteer: {
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

const authState = {
  started: false,
  ready: false,
  authenticated: false,
  latestQr: "",
  latestQrAt: 0,
  latestQrDataUrl: "",
};

const requireToken = (req, res) => {
  const token = req.get("X-WA-Bridge-Token") || "";
  if (BRIDGE_TOKEN && token !== BRIDGE_TOKEN) {
    res.status(401).json({ ok: false, error: "Unauthorized" });
    return false;
  }
  return true;
};

client.on("qr", (qr) => {
  console.log("Scan this QR with WhatsApp:");
  qrcodeTerminal.generate(qr, { small: true });
  authState.latestQr = qr;
  authState.latestQrAt = Date.now();
  authState.latestQrDataUrl = "";
  qrcode
    .toDataURL(qr, { margin: 1, width: 280 })
    .then((dataUrl) => {
      authState.latestQrDataUrl = dataUrl;
    })
    .catch(() => {
      authState.latestQrDataUrl = "";
    });
});

client.on("authenticated", () => {
  authState.authenticated = true;
});

client.on("auth_failure", () => {
  authState.authenticated = false;
});

client.on("ready", () => {
  console.log("WhatsApp client ready");
  authState.ready = true;
  authState.authenticated = true;
});

const resetAuthState = () => {
  authState.ready = false;
  authState.authenticated = false;
  authState.latestQr = "";
  authState.latestQrAt = 0;
  authState.latestQrDataUrl = "";
};

const startClient = async () => {
  if (authState.started) return;
  authState.started = true;
  resetAuthState();
  client.initialize();
};

const stopClient = async () => {
  if (!authState.started) return;
  try {
    await client.destroy();
  } catch (err) {
    console.error("Destroy error:", err.message || err);
  }
  authState.started = false;
  resetAuthState();
};

client.on("message", async (msg) => {
  if (!msg || !msg.from || !msg.body) return;
  try {
    const resp = await fetch(AGENT_WEBHOOK, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(BRIDGE_TOKEN ? { "X-WA-Bridge-Token": BRIDGE_TOKEN } : {}),
      },
      body: JSON.stringify({ from: msg.from, body: msg.body }),
    });
    if (!resp.ok) {
      console.error("Agent webhook error:", resp.status, await resp.text());
    }
  } catch (err) {
    console.error("Webhook error:", err.message || err);
  }
});

app.get("/health", (req, res) => {
  if (!requireToken(req, res)) return;
  res.json({
    ok: true,
    started: authState.started,
    authenticated: authState.authenticated,
    ready: authState.ready,
  });
});

app.get("/auth/status", (req, res) => {
  if (!requireToken(req, res)) return;
  const info = client.info || null;
  res.json({
    ok: true,
    started: authState.started,
    authenticated: authState.authenticated,
    ready: authState.ready,
    has_qr: Boolean(authState.latestQr),
    latest_qr_at: authState.latestQrAt,
    client: info
      ? {
          wid: info.wid?._serialized || "",
          pushname: info.pushname || "",
        }
      : null,
  });
});

app.post("/auth/start", async (req, res) => {
  if (!requireToken(req, res)) return;
  try {
    await startClient();
    res.json({ ok: true, started: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.post("/auth/stop", async (req, res) => {
  if (!requireToken(req, res)) return;
  await stopClient();
  res.json({ ok: true, started: false });
});

app.post("/auth/restart", async (req, res) => {
  if (!requireToken(req, res)) return;
  await stopClient();
  try {
    await startClient();
    res.json({ ok: true, started: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.get("/auth/qr", (req, res) => {
  if (!requireToken(req, res)) return;
  if (!authState.latestQr) {
    res.status(404).json({ ok: false, error: "No QR available" });
    return;
  }
  res.json({
    ok: true,
    qr: authState.latestQr,
    data_url: authState.latestQrDataUrl,
    latest_qr_at: authState.latestQrAt,
  });
});

app.post("/auth/logout", async (req, res) => {
  if (!requireToken(req, res)) return;
  try {
    await client.logout();
  } catch (err) {
    console.error("Logout error:", err.message || err);
  }
  await stopClient();
  try {
    await fs.rm(path.resolve(AUTH_PATH), { recursive: true, force: true });
    await fs.rm(path.resolve(CACHE_PATH), { recursive: true, force: true });
  } catch (err) {
    console.error("Auth cache cleanup error:", err.message || err);
  }
  res.json({ ok: true });
});

app.post("/send", async (req, res) => {
  if (!requireToken(req, res)) return;
  const { to, text } = req.body || {};
  if (!to || !text) {
    res.status(400).json({ ok: false, error: "Missing 'to' or 'text'" });
    return;
  }
  try {
    await client.sendMessage(to, text);
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message || String(err) });
  }
});

app.listen(PORT, () => {
  console.log(`WhatsApp bridge listening on :${PORT}`);
  console.log(`Posting inbound messages to ${AGENT_WEBHOOK}`);
  if (AUTO_START) {
    startClient();
  }
});
