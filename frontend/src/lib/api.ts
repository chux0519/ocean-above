import type { AppConfig, Conversation, ConversationSummary, Me, Message, SseEvent, TicketCard, Trace, User } from "./types";
import { readSse } from "./sse";

type ErrorBody = { code?: string; message?: string; scope?: string; resets_at?: string };

export class ApiError extends Error {
  /** 429 rate_limited only: which limit ("user" daily messages or "site" token budget) and when it resets. */
  scope?: "user" | "site";
  resetsAt?: string;
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

function toError(res: Response, body: unknown): ApiError {
  const e = (body as { error?: ErrorBody }).error;
  const err = new ApiError(res.status, e?.code ?? "error", e?.message ?? res.statusText);
  if (e?.scope === "user" || e?.scope === "site") err.scope = e.scope;
  if (typeof e?.resets_at === "string") err.resetsAt = e.resets_at;
  return err;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw toError(res, body);
  return body as T;
}

export const api = {
  config: () => req<AppConfig>("/config"),
  login: (username: string, password: string) =>
    req<User>("/auth/dev", { method: "POST", body: JSON.stringify({ username, password }) }),
  loginGoogle: (credential: string) =>
    req<User>("/auth/google", { method: "POST", body: JSON.stringify({ credential }) }),
  loginInvite: (code: string, name: string) =>
    req<User>("/auth/invite", { method: "POST", body: JSON.stringify({ code, name }) }),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => req<Me>("/me"),
  conversations: () => req<{ conversations: ConversationSummary[] }>("/conversations").then((r) => r.conversations),
  createConversation: () => req<ConversationSummary>("/conversations", { method: "POST", body: "{}" }),
  conversation: (id: string) => req<Conversation>(`/conversations/${id}`),
  book: (quoteId: string, nonce: string) =>
    req<{ ticket: TicketCard; message: Message }>(`/quotes/${quoteId}/book`, {
      method: "POST",
      body: JSON.stringify({ nonce }),
    }),
  trace: (quoteId: string) => req<Trace>(`/quotes/${quoteId}/trace`),
  tickets: () => req<{ tickets: TicketCard[] }>("/tickets").then((r) => r.tickets),

  /** POST a user message and stream SSE events. Resolves when the stream ends. */
  async sendMessage(conversationId: string, text: string, onEvent: (e: SseEvent) => void, signal?: AbortSignal) {
    const res = await fetch(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
      signal,
    });
    if (!res.ok || !res.body) {
      throw toError(res, await res.json().catch(() => ({})));
    }
    await readSse(res.body, onEvent);
  },
};
