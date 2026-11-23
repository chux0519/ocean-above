import { describe, expect, it } from "vitest";
import { SseParser, readSse } from "@/lib/sse";
import { applyEvent, type ChatState } from "@/lib/chat";
import type { SseEvent } from "@/lib/types";
import { fixtureText } from "./fixtures";

const raw = fixtureText("sse_quote_turn.txt");

describe("SSE parser", () => {
  it("parses all events of the quote turn fixture", () => {
    const evs = new SseParser().push(raw);
    expect(evs.map((e) => e.event)).toEqual(["user_message", "status", "card", "delta", "delta", "ping", "done"]);
  });

  it("is robust to arbitrary chunk boundaries", () => {
    for (const size of [1, 3, 17, 100]) {
      const p = new SseParser();
      const out: SseEvent[] = [];
      for (let i = 0; i < raw.length; i += size) out.push(...p.push(raw.slice(i, i + size)));
      expect(out).toHaveLength(7);
    }
  });

  it("reads a ReadableStream body", async () => {
    const bytes = new TextEncoder().encode(raw);
    const body = new ReadableStream<Uint8Array>({
      start(c) { c.enqueue(bytes.slice(0, 50)); c.enqueue(bytes.slice(50)); c.close(); },
    });
    const evs: SseEvent[] = [];
    await readSse(body, (e) => evs.push(e));
    expect(evs.at(-1)?.event).toBe("done");
  });
});

describe("chat reducer on the fixture turn", () => {
  it("builds the draft, then done replaces it with the server message", () => {
    let s: ChatState = { messages: [], draft: null };
    const evs = new SseParser().push(raw);
    for (const e of evs.slice(0, 5)) s = applyEvent(s, e);
    expect(s.messages).toHaveLength(1);
    expect(s.draft?.cards).toHaveLength(1);
    expect(s.draft?.cards[0].type === "quote" && s.draft.cards[0].state.bookable).toBe(false);
    expect(s.draft?.text).toContain("8,928.6 km for $1,160.71");
    s = applyEvent(s, evs.at(-1)!);
    expect(s.draft).toBeNull();
    expect(s.messages).toHaveLength(2);
    const card = s.messages[1].cards[0];
    expect(card.type === "quote" && card.state.bookable).toBe(true);
  });

  it("replace overwrites text; error keeps the user message", () => {
    let s: ChatState = { messages: [], draft: null };
    s = applyEvent(s, { event: "user_message", data: { id: "m_1", role: "user", text: "hi", cards: [], created_at: "" } });
    s = applyEvent(s, { event: "delta", data: { text: "bad" } });
    s = applyEvent(s, { event: "replace", data: { text: "template" } });
    expect(s.draft?.text).toBe("template");
    s = applyEvent(s, { event: "error", data: { code: "llm_unavailable", message: "down" } });
    expect(s.messages).toHaveLength(1);
    expect(s.draft?.error).toBe("down");
  });
});
