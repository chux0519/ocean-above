import type { Card, Message, SseEvent } from "./types";

export type Draft = { text: string; status: string | null; cards: Card[]; error: string | null };
export type ChatState = { messages: Message[]; draft: Draft | null };

export const emptyDraft: Draft = { text: "", status: null, cards: [], error: null };

/** Pure reducer: apply one SSE event. `done` replaces the draft by the final server message. */
export function applyEvent(s: ChatState, e: SseEvent): ChatState {
  const d = s.draft ?? emptyDraft;
  switch (e.event) {
    case "user_message": return { messages: [...s.messages, e.data], draft: { ...emptyDraft } };
    case "status": return { ...s, draft: { ...d, status: e.data.text } };
    case "card": return { ...s, draft: { ...d, cards: [...d.cards, e.data.card] } };
    case "delta": return { ...s, draft: { ...d, text: d.text + e.data.text } };
    case "replace": return { ...s, draft: { ...d, text: e.data.text } };
    case "done": return { messages: [...s.messages, e.data.message], draft: null };
    case "error": return { ...s, draft: { ...d, status: null, error: e.data.message } };
    default: return s;
  }
}
