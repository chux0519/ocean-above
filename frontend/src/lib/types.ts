// Mirrors docs/api/contract.md. Keep in sync through the coordinator only.
export type User = { id: string; display_name: string; auth_provider: "dev" | "google" | "invite"; email: string | null };
export type Usage = { messages_today: number; daily_limit: number; resets_at: string };
export type Me = User & { usage: Usage };
export type AppConfig = { dev_login: boolean; invite_login: boolean; google_client_id: string | null };
export type ConversationSummary = { id: string; title: string; created_at: string; updated_at: string; preview: string };
export type Leg = { from: string; to: string; distance_display: string; distance: string };
export type CardState = {
  status: "quoted" | "presented" | "booked" | "expired" | "stale";
  bookable: boolean;
  nonce: string | null;
  ticket_id: string | null;
};
type RouteFields = {
  id: string; origin: string; destination: string; legs: Leg[]; transfers: string[];
  distance_display: string; price_display: string; distance: string; price: string;
  expires_at: string; state: CardState;
  similar_names?: { city: string; connected_hubs: string[] }[];
};
export type QuoteCard = RouteFields & { type: "quote" };
export type BookingRequestCard = RouteFields & { type: "booking_request" };
export type TicketCard = {
  type: "ticket"; id: string; quote_id: string; origin: string; destination: string; legs: Leg[];
  transfers: string[]; distance_display: string; price_display: string; booked_at: string;
};
export type TicketListCard = { type: "ticket_list"; count: number; filter: Record<string, string>; tickets: TicketCard[] };
export type NoServiceCard = { type: "no_service"; city: string; nearest: { city: string; distance_display: string }[] };
export type AmbiguousCityCard = {
  type: "ambiguous_city"; query: string;
  candidates: { city: string; is_hub: boolean; connected_hubs: string[] }[];
};
export type UnknownCityCard = { type: "unknown_city"; query: string; suggestions: string[] };
export type NoticeCard = {
  type: "notice"; code: "same_city" | "unreachable" | "quote_expired" | "stale"; message: string;
};
export type Card =
  | QuoteCard | BookingRequestCard | TicketCard | TicketListCard | NoServiceCard
  | AmbiguousCityCard | UnknownCityCard | NoticeCard;
export type Message = {
  id: string; role: "user" | "assistant" | "system"; text: string; cards: Card[]; created_at: string;
  /** v1.6: "cancelled" = the user stopped this reply (Stop). Older servers omit it. */
  status?: "completed" | "cancelled";
};
/** v1.6: state of a run after `POST /conversations/{id}/runs/{run_id}/cancel`. */
export type RunStatus = "queued" | "running" | "cancelling" | "finishing" | "cancelled" | "completed" | "failed";
export type Conversation = ConversationSummary & { messages: Message[] };
export type Trace = {
  quote_id: string; formula: string; algorithm: string; network_fingerprint: string;
  legs: (Leg & { from_coord: [number, number]; to_coord: [number, number]; source: string })[];
  distance: string; price: string; distance_display: string; price_display: string; note: string;
};

export type SseEvent =
  | { event: "user_message"; data: Message & { run_id?: string } }
  | { event: "status"; data: { text: string } }
  | { event: "card"; data: { card: Card } }
  | { event: "delta"; data: { text: string } }
  | { event: "replace"; data: { text: string } }
  | { event: "ping"; data: Record<string, never> }
  | { event: "done"; data: { message: Message } }
  | { event: "cancelled"; data: { run_id: string; message: Message | null } }
  | { event: "error"; data: { code: string; message: string; run_id?: string } };
