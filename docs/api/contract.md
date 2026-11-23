# API and card contract (v1.3, 2026-11-23)

Single source for the frontend (T6) and backend (T2/T3/T5). Change it only through the coordinator. Examples live in
`contract/fixtures/`; both sides' tests load them. Numbers in fixtures are **illustrative** (computed with a quick
script, not by the production core) — correctness is tested in the backend, not by these files.

## Conventions
- Same origin, JSON, paths under `/api`. Session cookie `oa_session` (httpOnly, SameSite=Lax, `Secure` by env).
- Times: ISO 8601 UTC strings. IDs: opaque strings with a type prefix (`u_`, `c_`, `m_`, `q_`, `t_`).
- **The frontend never formats or computes numbers.** Every number shown comes from a `*_display` field.
  High-precision values (`distance`, `price`, decimal strings) are only shown in the trace view.
- Errors: HTTP status + `{"error": {"code": "<snake_case>", "message": "<human text>"}}`.
  Common codes: `unauthorized` (401), `not_found` (404; also for other users' objects), `bad_request` (400),
  `rate_limited` (429), `llm_unavailable` (503).
- Login endpoints are throttled per client IP (10 / minute) → 429 `rate_limited`.
- Request bodies over 64 KB → 413 `payload_too_large`. Non-GET requests whose `Sec-Fetch-Site` is `cross-site` /
  `same-site`, or whose `Origin` does not match the host → 403 `forbidden_origin`.
- Session cookie `oa_session` = `user.epoch.expires.sig`; `POST /api/auth/logout` revokes **all** sessions of the user.
- Invite login: one account per invite code (the code identifies the account; `name` is only the display name).
- `POST /api/conversations/{id}/messages` returns **429 `rate_limited` before any SSE** when the user's daily message
  limit or the site's daily token budget is reached; nothing is stored; `error.message` says which limit and when it
  resets (UTC midnight), and the body adds `"scope": "user" | "site"`, `"resets_at"`. Booking, tickets, history
  are never limited.

## Endpoints

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/api/health` | – | `{"status":"ok","network_fingerprint":"sha256:…"}` |
| GET | `/api/config` | – | `{"dev_login":bool,"invite_login":bool,"google_client_id":string\|null}` |
| POST | `/api/auth/dev` | `{"username","password"}` | `User` + sets cookie; 401 `invalid_credentials`; 404 `not_found` when disabled |
| POST | `/api/auth/google` | `{"credential"}` (GIS ID token) | `User` + sets cookie; 401 `invalid_credentials` (bad signature / aud / iss / expired / email not verified); 404 when not configured; 503 `auth_unavailable` (Google keys unreachable) |
| POST | `/api/auth/invite` | `{"code","name"}` (name 1–40 chars) | `User` + sets cookie; 401 `invalid_credentials`; 404 when disabled |
| POST | `/api/auth/logout` | – | `{"ok":true}` |
| GET | `/api/me` | – | `User & {"usage": Usage}` or 401 |
| GET | `/api/conversations` | – | `{"conversations":[ConversationSummary]}` newest first |
| POST | `/api/conversations` | `{}` | `ConversationSummary` |
| GET | `/api/conversations/{id}` | – | `Conversation` (messages oldest first; card states are current server state) |
| POST | `/api/conversations/{id}/messages` | `{"text"}` | **SSE stream** (below) |
| POST | `/api/quotes/{id}/book` | `{"nonce"}` | `{"ticket": TicketCard, "message": Message}` or error (below) |
| GET | `/api/quotes/{id}/trace` | – | `Trace` |
| GET | `/api/tickets` | – | `{"tickets":[TicketCard]}` newest first |
| GET | `/api/debug/sse-ping` | – | SSE: `ping` with `{"seq":n}` immediately, then every 2 s, 5 events total |

Booking errors: `invalid_nonce` (409), `not_presented` (409), `expired` (409), `already_booked` (409),
`stale` (409; data changed — ask for a new quote), `not_found` (404), `conversation_busy` (409; a reply is still
being generated — retry after `done`), `data_unavailable` (503; route data could not be read — retry; the quote and
nonce are unchanged).

## Objects

```ts
type User = { id: string; display_name: string; auth_provider: "dev" | "google" | "invite"; email: string | null }
type Usage = { messages_today: number; daily_limit: number; resets_at: string }
type ConversationSummary = { id: string; title: string /* never empty: first user message, else "New conversation" */
  created_at: string; updated_at: string; preview: string }
type Conversation = ConversationSummary & { messages: Message[] }
type Message = {
  id: string; role: "user" | "assistant" | "system"   // system = booking results etc.
  text: string                                        // markdown for assistant; plain for user
  cards: Card[]; created_at: string
}
type Leg = { from: string; to: string; distance_display: string; distance: string }
type CardState = { status: "quoted" | "presented" | "booked" | "expired" | "stale";
                   bookable: boolean; nonce: string | null; ticket_id: string | null }

type Card = QuoteCard | BookingRequestCard | TicketCard | TicketListCard | NoServiceCard
          | AmbiguousCityCard | UnknownCityCard | NoticeCard

type QuoteCard = { type: "quote"; id: string /* q_ */; origin: string; destination: string
  legs: Leg[]; transfers: string[]; distance_display: string; price_display: string
  distance: string; price: string; expires_at: string; state: CardState
  similar_names: { city: string; connected_hubs: string[] }[] }   // other cities equal to an endpoint after accent
                                                                   // folding (San Jose / San José); [] normally
type BookingRequestCard = Omit<QuoteCard, "type"> & { type: "booking_request" }   // id is the quote id
type TicketCard = { type: "ticket"; id: string /* t_ */; quote_id: string; origin: string; destination: string
  legs: Leg[]; transfers: string[]; distance_display: string; price_display: string; booked_at: string }
type TicketListCard = { type: "ticket_list"; count: number; filter: Record<string, string>; tickets: TicketCard[] }
type NoServiceCard = { type: "no_service"; city: string
  nearest: { city: string; distance_display: string }[] }          // up to 3, nearest first
type AmbiguousCityCard = { type: "ambiguous_city"; query: string
  candidates: { city: string; is_hub: boolean; connected_hubs: string[] }[] }
type UnknownCityCard = { type: "unknown_city"; query: string; suggestions: string[] }
type NoticeCard = { type: "notice"; code: "same_city" | "unreachable" | "quote_expired" | "stale"; message: string }

type Trace = { quote_id: string; formula: string; algorithm: string; network_fingerprint: string
  legs: (Leg & { from_coord: [number, number]; to_coord: [number, number]; source: string })[]
  distance: string; price: string; distance_display: string; price_display: string; note: string }
```

Card buttons: a quote or booking-request card shows **Confirm booking** only when `state.bookable` is true; clicking
posts `{"nonce": state.nonce}` to `/api/quotes/{id}/book`. While a stream is running, buttons stay disabled until
`done`; after `done` (or on reload) the card state from the server wins.

## SSE stream (`POST /api/conversations/{id}/messages`)

`Content-Type: text/event-stream`. Each event: `event: <name>\ndata: <json>\n\n`.

| event | data | notes |
|---|---|---|
| `user_message` | `Message` | the stored user message, first event |
| `status` | `{"text":"Looking up routes…"}` | progress, replaceable |
| `card` | `{"card": Card}` | push immediately; state.bookable is false until `done` |
| `delta` | `{"text":"…"}` | append; already validated text (one sentence or more) |
| `replace` | `{"text":"…"}` | replace the whole assistant text so far |
| `ping` | `{}` | heartbeat every 15 s; ignore |
| `done` | `{"message": Message}` | final assistant message incl. cards with current states |
| `error` | `{"code","message"}` | stream ends; the user message is kept, **except** `conversation_busy` (another turn holds the conversation; sent before `user_message`, nothing stored — keep the input box text and retry) |

Example: `contract/fixtures/sse_quote_turn.txt`.
