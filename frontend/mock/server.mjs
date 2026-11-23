// Tiny mock API serving contract/fixtures. `node mock/server.mjs` (default :8000).
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../contract/fixtures");
const json = (n) => JSON.parse(fs.readFileSync(path.join(dir, `${n}.json`), "utf8"));
const port = Number(process.env.MOCK_PORT ?? 8000);
let loggedIn = false;
let booked = false;

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

const extra = [];
function conversation() {
  const c = json("conversation");
  const msgs = extra.map((m) => {
    if (!booked) return m;
    return { ...m, cards: m.cards.map((k) => (k.state ? { ...k, state: { status: "booked", bookable: false, nonce: null, ticket_id: "t_91b2" } } : k)) };
  });
  c.messages = [...c.messages, ...msgs];
  return c;
}

function sseTurn(res) {
  const raw = fs.readFileSync(path.join(dir, "sse_quote_turn.txt"), "utf8");
  res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
  extra.length = 0; booked = false;
  // Unique ids per turn (the fixture reuses m_1/m_2 which already exist in the stored conversation).
  const blocks = raw.split("\n\n").filter(Boolean).map((blk) => {
    const ev = /^event: (.*)$/m.exec(blk)?.[1];
    if (ev !== "user_message" && ev !== "done") return blk;
    const d = JSON.parse(/^data: (.*)$/m.exec(blk)[1]);
    const m = ev === "done" ? d.message : d;
    m.id = `${m.id}_t${extra.length}`;
    extra.push(m);
    return `event: ${ev}\ndata: ${JSON.stringify(d)}`;
  });
  let i = 0;
  const t = setInterval(() => {
    if (i >= blocks.length) { clearInterval(t); res.end(); return; }
    res.write(blocks[i++] + "\n\n");
  }, 400);
}

http.createServer((req, res) => {
  const url = new URL(req.url, "http://x");
  const p = url.pathname;
  const auth = loggedIn || process.env.MOCK_OPEN === "1";
  if (p === "/api/health") return send(res, 200, { status: "ok", network_fingerprint: "sha256:mock" });
  if (p === "/api/config") return send(res, 200, json("config"));
  if (p === "/api/auth/dev" && req.method === "POST") { loggedIn = true; return send(res, 200, json("me")); }
  if (p === "/api/auth/logout") { loggedIn = false; return send(res, 200, { ok: true }); }
  if (!auth) return send(res, 401, { error: { code: "unauthorized", message: "Please sign in" } });
  if (p === "/api/me") return send(res, 200, json("me"));
  if (p === "/api/conversations" && req.method === "GET") return send(res, 200, json("conversations"));
  if (p === "/api/conversations") return send(res, 200, { id: "c_new", title: "", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), preview: "" });
  if (/^\/api\/conversations\/[^/]+$/.test(p)) return send(res, 200, conversation());
  if (/\/messages$/.test(p)) return sseTurn(res);
  if (/^\/api\/quotes\/[^/]+\/trace$/.test(p)) return send(res, 200, json("trace"));
  if (/^\/api\/quotes\/[^/]+\/book$/.test(p)) { booked = true; return send(res, 200, json("booking_ok")); }
  if (p === "/api/tickets") return send(res, 200, json("tickets"));
  send(res, 404, { error: { code: "not_found", message: "not found" } });
}).listen(port, "0.0.0.0", () => console.log(`mock api on :${port}`));
