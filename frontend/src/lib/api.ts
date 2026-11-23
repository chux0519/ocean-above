import type { Conversation, ConversationSummary, Message, SseEvent, TicketCard, Trace, User } from "./types";
import { readSse } from "./sse";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const e = (body as { error?: { code?: string; message?: string } }).error;
    throw new ApiError(res.status, e?.code ?? "error", e?.message ?? res.statusText);
  }
  return body as T;
}

export const api = {
  config: () => req<{ dev_login: boolean; google_client_id: string | null }>("/config"),
  login: (username: string, password: string) =>
    req<User>("/auth/dev", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () => req<User>("/me"),
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
      const body = await res.json().catch(() => ({}));
      const e = (body as { error?: { code?: string; message?: string } }).error;
      throw new ApiError(res.status, e?.code ?? "error", e?.message ?? res.statusText);
    }
    await readSse(res.body, onEvent);
  },
};
