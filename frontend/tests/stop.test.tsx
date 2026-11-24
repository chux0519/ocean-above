import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtureText } from "./fixtures";
import { SseParser } from "@/lib/sse";
import { applyEvent } from "@/lib/chat";
import { MessageList } from "@/components/message-list";
import type { Message, SseEvent } from "@/lib/types";

const refreshUser = vi.fn();
vi.mock("@/components/shell", () => ({
  Shell: ({ children }: { children: (s: unknown) => React.ReactNode }) =>
    <>{children({ user: {}, refreshUser, sidebarSlot: document.body, closeNav: () => {} })}</>,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }), usePathname: () => "/chat" }));
import ChatPage, { STOP_GRACE_MS } from "@/app/chat/page";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const enc = new TextEncoder();
const sse = (event: string, data: unknown) => enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

function fixtureEvents(name: string): SseEvent[] {
  return new SseParser().push(fixtureText(name));
}

const stoppedTurn = fixtureEvents("sse_cancelled_turn.txt");
const userMessage = stoppedTurn[0].data as Message & { run_id: string };
const stoppedMessage = (stoppedTurn.at(-1)!.data as { message: Message }).message;
const RUN = userMessage.run_id;

/** A fake server: one conversation; the message stream is pushed by the test; abort errors the stream. */
function server({ headersLate = false } = {}) {
  const calls: string[] = [];
  let push!: (chunk: Uint8Array) => void;
  let end!: () => void;
  let release: () => void = () => {};
  const conv = { id: "c_1", title: "Trip", created_at: "", updated_at: "", preview: "" };
  let messages: Message[] = [];
  let aborted = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url}`);
    if (url === "/api/conversations" && method === "GET") return json({ conversations: [conv, { ...conv, id: "c_2", title: "Other" }] });
    if (url === "/api/conversations/c_1") return json({ ...conv, messages });
    if (url === "/api/conversations/c_2") return json({ ...conv, id: "c_2", messages: [] });
    if (url.endsWith(`/runs/${RUN}/cancel`)) return json({ run_id: RUN, status: "cancelling" });
    if (url.endsWith("/messages")) {
      const body = new ReadableStream<Uint8Array>({
        start(c) {
          push = (chunk) => c.enqueue(chunk);
          end = () => c.close();
          init?.signal?.addEventListener("abort", () => { aborted = true; c.error(new DOMException("aborted", "AbortError")); });
        },
      });
      const response = new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream", "X-Run-Id": RUN } });
      if (!headersLate) return response;
      return new Promise<Response>((resolve) => { release = () => resolve(response); });
    }
    return json({ error: { code: "not_found", message: "nf" } }, 404);
  }));
  return {
    calls,
    send: (event: string, data: unknown) => act(async () => { push(sse(event, data)); }),
    end: () => act(async () => { end(); }),
    release: () => act(async () => { release(); }),
    setMessages: (m: Message[]) => { messages = m; },
    get aborted() { return aborted; },
  };
}

async function sendText(text: string) {
  const input = await screen.findByLabelText("Message");
  await waitFor(() => expect(screen.queryByText("Loading conversation…")).toBeNull());
  await userEvent.type(input, text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  return input;
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); refreshUser.mockReset(); });

describe("Stop", () => {
  it("is offered while waiting for the first token, stops the server run, and shows the stopped reply", async () => {
    const srv = server();
    render(<ChatPage />);
    await sendText("Quote Guangzhou to Lima again, please");
    // no event yet: Send has become Stop
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
    await srv.send("user_message", userMessage);
    await srv.send("delta", { text: "The cheapest route goes through " });
    await userEvent.click(stop);
    await waitFor(() => expect(srv.calls).toContain(`POST /api/conversations/c_1/runs/${RUN}/cancel`));
    expect(screen.getByRole("button", { name: "Stopping" })).toBeDisabled();
    // the view is frozen: later deltas are not shown
    await srv.send("delta", { text: "never shown. " });
    expect(screen.queryByText(/never shown/)).toBeNull();
    srv.setMessages([userMessage, stoppedMessage]);
    await srv.send("cancelled", { run_id: RUN, message: stoppedMessage });
    await srv.end();
    // authoritative reload, marked as stopped, the card not bookable, no error, Send back
    await waitFor(() => expect(screen.getByTestId("stopped-marker")).toHaveTextContent("Stopped"));
    expect(srv.calls.filter((c) => c === "GET /api/conversations/c_1").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByTestId("stopped-note")).toHaveTextContent("ask again to book");
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("error")).toBeNull();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("before the response headers arrive: the cancel is sent as soon as the run id is known", async () => {
    const srv = server({ headersLate: true });
    render(<ChatPage />);
    await sendText("hi");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    expect(srv.calls.some((c) => c.includes("/cancel"))).toBe(false);
    await srv.release();
    await waitFor(() => expect(srv.calls).toContain(`POST /api/conversations/c_1/runs/${RUN}/cancel`));
    await srv.send("cancelled", { run_id: RUN, message: null });
    await srv.end();
    await screen.findByText("Stopped — your message was not sent");
  });

  it("stopped before the message was stored: input kept, no reload, no error", async () => {
    const srv = server();
    render(<ChatPage />);
    const input = await sendText("Fly me to Lima");
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(srv.calls.some((c) => c.includes("/cancel"))).toBe(true));
    await srv.send("cancelled", { run_id: RUN, message: null });
    await srv.end();
    await screen.findByText("Stopped — your message was not sent");
    expect(input).toHaveValue("Fly me to Lima");
    expect(srv.calls.filter((c) => c === "GET /api/conversations/c_1")).toHaveLength(1); // only the first load
    expect(screen.queryByTestId("error")).toBeNull();
    await waitFor(() => expect(refreshUser).toHaveBeenCalled()); // the slot was given back
  });

  it("closes the stream after a grace period when the server does not end it, without a network error", async () => {
    const srv = server();
    render(<ChatPage />);
    await sendText("hi");
    await srv.send("user_message", userMessage);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await userEvent.click(await screen.findByRole("button", { name: "Stop" }));
    await waitFor(() => expect(srv.calls.some((c) => c.includes("/cancel"))).toBe(true));
    srv.setMessages([userMessage, stoppedMessage]);
    await act(async () => { vi.advanceTimersByTime(STOP_GRACE_MS + 10); });
    await waitFor(() => expect(srv.aborted).toBe(true));
    await screen.findByTestId("stopped-marker");
    expect(screen.queryByText(/Connection lost/)).toBeNull();
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("switching conversations detaches the stream: no stale updates, no Stop sent, Send available", async () => {
    const srv = server();
    render(<ChatPage />);
    await sendText("hi");
    await srv.send("user_message", userMessage);
    await userEvent.click(screen.getByRole("button", { name: /Other/ }));
    await waitFor(() => expect(screen.queryByText(userMessage.text)).toBeNull());
    expect(srv.aborted).toBe(true); // the reader is closed; the run itself goes on (disconnect != Stop)
    expect(srv.calls.some((c) => c.includes("/cancel"))).toBe(false);
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
    expect(screen.queryByText(/Connection lost/)).toBeNull();
  });
});

describe("stopped replies", () => {
  it("the reducer stores the stopped reply in place of the draft", () => {
    let s = { messages: [] as Message[], draft: null as ReturnType<typeof applyEvent>["draft"] };
    for (const e of stoppedTurn) s = applyEvent(s, e);
    expect(s.draft).toBeNull();
    expect(s.messages.map((m) => m.status)).toEqual(["completed", "cancelled"]);
    expect(applyEvent({ messages: [], draft: { text: "x", status: null, cards: [], error: null } },
      { event: "cancelled", data: { run_id: RUN, message: null } })).toEqual({ messages: [], draft: null });
  });

  it("a stopped reply is marked, its quote card has no Confirm button even if a state said bookable", () => {
    const card = { ...stoppedMessage.cards[0], state: { status: "presented", bookable: true, nonce: "n_x", ticket_id: null } };
    render(<MessageList messages={[userMessage, { ...stoppedMessage, cards: [card as Message["cards"][0]] }]} />);
    const list = screen.getByTestId("messages");
    expect(within(list).getByTestId("stopped-marker")).toHaveTextContent("Stopped");
    expect(within(list).getByTestId("stopped-note")).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Confirm booking" })).toBeNull();
  });

  it("a stopped reply without text says so", () => {
    render(<MessageList messages={[{ ...stoppedMessage, text: "", cards: [] }]} />);
    expect(screen.getByTestId("stopped-marker")).toHaveTextContent("Stopped before a reply");
  });

  it("completed replies have no marker", () => {
    render(<MessageList messages={[{ ...stoppedMessage, status: "completed" }]} />);
    expect(screen.queryByTestId("stopped-marker")).toBeNull();
  });
});
