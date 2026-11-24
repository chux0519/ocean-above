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
let forced = null; // status a refused quote ends in (stale / expired / booked)

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

// Scenario switches for screenshots/tests: POST /__mock {"book":"stale","message":"busy","tickets":"empty","history":"error","delay":800,"conversation":"error"}
// #55: message: headers_late (the response headers come after 10 s: Stop has no run id); switch: {"slow":"<id>","ms":N}
// (that conversation loads N ms late; every other conversation shows its own short exchange)
// login: google|invite|dev (only that method; default = config fixture); message: limit|site_limit (429)|server_busy
// confirm: multileg; scene: proposal; book: stale|expired|invalid_nonce|already_booked|conversation_busy|data_unavailable; message: busy|drop; tickets/history/conversation: empty|error
let scn = {};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const extra = [];
function conversation() {
  const c = json("conversation");
  const msgs = extra.map((m) => {
    if (!booked) return m;
    return { ...m, cards: m.cards.map((k) => (k.state ? { ...k, state: { status: "booked", bookable: false, nonce: null, ticket_id: "t_91b2" } } : k)) };
  });
  c.messages = [...c.messages, ...msgs];
  // scenario {"scene":"proposal"} (07-ui): the first exchange only — a multi-leg quote (not booked) then its
  // booking request awaiting confirmation.
  if (scn.scene === "proposal") {
    c.messages = c.messages.slice(0, 4);
    for (const m of c.messages) for (const k of m.cards) {
      if (k.type === "quote") k.state = { status: "quoted", bookable: false, nonce: null, ticket_id: null };
      if (k.type === "booking_request") k.state = { status: "presented", bookable: true, nonce: "n_mock_ml", ticket_id: null };
    }
  }
  // Presented quotes in the fixture expired long ago; keep them live so the countdown and Confirm show.
  const soon = new Date(Date.now() + 10 * 60_000).toISOString();
  for (const m of c.messages) for (const k of m.cards) {
    // scenario {"confirm":"multileg"}: the multi-leg booking_request is still awaiting confirmation
    if (scn.confirm === "multileg" && k.type === "booking_request") k.state = { status: "presented", bookable: true, nonce: "n_mock_ml", ticket_id: null };
    if (k.state?.status !== "presented") continue;
    k.expires_at = soon;
    if (forced) k.state = { status: forced, bookable: false, nonce: null, ticket_id: forced === "booked" ? "t_91b2" : null };
  }
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

http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const p = url.pathname;
  if (p === "/__mock" && req.method === "POST") {
    let b = ""; for await (const c of req) b += c;
    scn = JSON.parse(b || "{}"); forced = null; return send(res, 200, scn);
  }
  if (scn.delay && p.startsWith("/api/") && !/messages$/.test(p)) await wait(scn.delay);
  if (scn.slow && p.startsWith(scn.slow)) await wait(60_000); // {"slow":"/api/tickets"}: hold one endpoint (loading states)
  const auth = loggedIn || process.env.MOCK_OPEN === "1";
  if (p === "/api/health") return send(res, 200, { status: "ok", network_fingerprint: "sha256:mock" });
  if (p === "/api/config") {
    const c = json("config");
    // MOCK_GOOGLE_CLIENT_ID: a real (public) client id so the GIS button renders on localhost; any credential is accepted here.
    if (process.env.MOCK_GOOGLE_CLIENT_ID) c.google_client_id = process.env.MOCK_GOOGLE_CLIENT_ID;
    if (scn.login) Object.assign(c, { dev_login: scn.login === "dev", invite_login: scn.login === "invite", google_client_id: scn.login === "google" ? c.google_client_id : null });
    return send(res, 200, c);
  }
  if (p === "/api/auth/dev" && req.method === "POST") { loggedIn = true; return send(res, 200, json("me")); }
  if (p === "/api/auth/invite" && req.method === "POST") {
    let b = ""; for await (const c of req) b += c;
    const { code, name } = JSON.parse(b || "{}");
    if (code !== "example-invite") return send(res, 401, { error: { code: "invalid_credentials", message: "That invite code is not valid." } });
    loggedIn = true; return send(res, 200, { ...json("me"), id: "u_inv", display_name: name, auth_provider: "invite", email: null });
  }
  if (p === "/api/auth/google" && req.method === "POST") {
    loggedIn = true; return send(res, 200, { ...json("me"), id: "u_g", display_name: "Example User", auth_provider: "google", email: "user@example.com" });
  }
  if (p === "/api/auth/logout") { loggedIn = false; return send(res, 200, { ok: true }); }
  if (!auth) return send(res, 401, { error: { code: "unauthorized", message: "Please sign in" } });
  if (p === "/api/me") return send(res, 200, json("me"));
  if (p === "/api/conversations" && req.method === "GET") {
    if (scn.history === "error") return send(res, 503, { error: { code: "data_unavailable", message: "Could not load conversations right now." } });
    if (scn.history === "empty") return send(res, 200, { conversations: [] });
    return send(res, 200, json("conversations"));
  }
  if (p === "/api/conversations") return send(res, 200, { id: "c_new", title: "", created_at: new Date().toISOString(), updated_at: new Date().toISOString(), preview: "" });
  if (/^\/api\/conversations\/[^/]+$/.test(p)) {
    const id = p.split("/").pop();
    if (scn.switch?.slow === id) await wait(scn.switch.ms ?? 3000);
    const first = json("conversations").conversations[0]?.id;
    if (scn.switch && id !== first) {
      const meta = json("conversations").conversations.find((c) => c.id === id) ?? { id, title: "New chat" };
      const at = "2026-11-23T08:00:00Z";
      return send(res, 200, { ...meta, messages: [
        { id: `m_${id}_1`, role: "user", text: `Question in “${meta.title}”`, cards: [], created_at: at },
        { id: `m_${id}_2`, role: "assistant", text: `This is the reply in “${meta.title}”.`, cards: [], created_at: at, status: "completed" },
      ] });
    }
    if (scn.conversation === "error") return send(res, 503, { error: { code: "data_unavailable", message: "Could not load this conversation." } });
    if (scn.conversation === "empty") return send(res, 200, { ...conversation(), messages: [] });
    return send(res, 200, conversation());
  }
  if (/\/messages$/.test(p)) {
    if (scn.message === "limit") return send(res, 429, json("rate_limited"));
    if (scn.message === "site_limit") return send(res, 429, { error: { code: "rate_limited", message: "The demo has used today's message budget. It resets at 00:00 UTC.", scope: "site", resets_at: "2026-11-24T00:00:00Z" } });
    if (scn.message === "server_busy") {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      return res.end('event: error\ndata: {"code":"server_busy","message":"Too many conversations are running right now."}\n\n');
    }
    if (scn.message === "busy") {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      return res.end('event: error\ndata: {"code":"conversation_busy","message":"A reply is still being generated."}\n\n');
    }
    if (scn.message === "headers_late") return void setTimeout(() => sseTurn(res), 10_000);
    if (scn.message === "drop") {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.write('event: user_message\ndata: {"id":"m_drop","role":"user","text":"hello","cards":[],"created_at":"2026-11-23T08:00:00Z"}\n\n');
      return setTimeout(() => res.destroy(), 300);
    }
    return sseTurn(res);
  }
  if (/^\/api\/quotes\/[^/]+\/trace$/.test(p)) return send(res, 200, json("trace"));
  if (/^\/api\/quotes\/[^/]+\/book$/.test(p)) {
    if (scn.book) {
      const st = { stale: 409, expired: 409, invalid_nonce: 409, already_booked: 409, conversation_busy: 409, data_unavailable: 503 }[scn.book] ?? 400;
      if (scn.book === "stale" || scn.book === "expired") forced = scn.book;
      if (scn.book === "already_booked") forced = "booked";
      return send(res, st, { error: { code: scn.book, message: `mock ${scn.book}` } });
    }
    booked = true; return send(res, 200, json("booking_ok"));
  }
  if (p === "/api/tickets") {
    if (scn.tickets === "error") return send(res, 503, { error: { code: "data_unavailable", message: "Could not load your tickets right now." } });
    if (scn.tickets === "empty") return send(res, 200, { tickets: [] });
    return send(res, 200, json("tickets"));
  }
  send(res, 404, { error: { code: "not_found", message: "not found" } });
}).listen(port, "0.0.0.0", () => console.log(`mock api on :${port}`));
