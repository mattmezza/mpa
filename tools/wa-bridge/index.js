import express from "express";
import qrcode from "qrcode-terminal";
import dotenv from "dotenv";
import whatsappWeb from "whatsapp-web.js";

dotenv.config();

const PORT = Number(process.env.PORT || 3001);
const AGENT_WEBHOOK = process.env.AGENT_WEBHOOK || "http://localhost:8000/webhook/whatsapp";
const BRIDGE_TOKEN = process.env.BRIDGE_TOKEN || "";

const app = express();
app.use(express.json());

const { Client, LocalAuth } = whatsappWeb;

const client = new Client({
  authStrategy: new LocalAuth(),
  puppeteer: {
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  },
});

client.on("qr", (qr) => {
  console.log("Scan this QR with WhatsApp:");
  qrcode.generate(qr, { small: true });
});

client.on("ready", () => {
  console.log("WhatsApp client ready");
});

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
  const token = req.get("X-WA-Bridge-Token") || "";
  if (BRIDGE_TOKEN && token !== BRIDGE_TOKEN) {
    res.status(401).json({ ok: false, error: "Unauthorized" });
    return;
  }
  res.json({ ok: true });
});

app.post("/send", async (req, res) => {
  const token = req.get("X-WA-Bridge-Token") || "";
  if (BRIDGE_TOKEN && token !== BRIDGE_TOKEN) {
    res.status(401).json({ ok: false, error: "Unauthorized" });
    return;
  }
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
});

client.initialize();
