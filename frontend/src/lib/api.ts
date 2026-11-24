import type { AppConfig, Conversation, ConversationSummary, Me, Message, RunStatus, SseEvent, TicketCard, Trace, User } from "./types";
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

  /** Stop a running reply (v1.6). The server's state of the run afterwards; 404 when it is unknown. */
  cancelRun: (conversationId: string, runId: string) =>
    req<{ run_id: string; status: RunStatus }>(`/conversations/${conversationId}/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      body: "{}",
    }),

  /** POST a user message and stream SSE events. Resolves when the stream ends. `onRun` gets the run id as soon as
   * the response headers arrive (`X-Run-Id`), before any event, so Stop works while the turn still waits. */
  async sendMessage(conversationId: string, text: string, onEvent: (e: SseEvent) => void, signal?: AbortSignal,
    onRun?: (runId: string) => void) {
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
    let runId = res.headers.get("X-Run-Id");
    if (runId) onRun?.(runId);
    await readSse(res.body, (e) => {
      const first = (e.data as { run_id?: unknown } | null)?.run_id;
      if (!runId && typeof first === "string") { runId = first; onRun?.(first); } // a proxy dropped the header
      onEvent(e);
    });
  },
};
