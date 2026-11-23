import { afterEach, describe, expect, it, vi } from "vitest";
import { Suspense } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CardView } from "@/components/cards";
import { fixtureJson, conversation, cardOf } from "./fixtures";

vi.mock("@/components/shell", () => ({ Shell: ({ children }: { children: (u: unknown) => React.ReactNode }) => <>{children({})}</> }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }), usePathname: () => "/" }));

import TicketsPage from "@/app/tickets/page";
import HistoryPage from "@/app/history/page";
import HistoryDetail from "@/app/history/[id]/page";
import ChatPage from "@/app/chat/page";

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const err = (status: number, code: string, message = code) => json({ error: { code, message } }, status);
/** Route table first match wins; unknown → 404. */
function mockFetch(...handlers: Handler[]) {
  const f = vi.fn(async (url: string, init?: RequestInit) => {
    for (const h of handlers) { const r = await h(url, init); if (r) return r; }
    return err(404, "not_found");
  });
  vi.stubGlobal("fetch", f);
  return f;
}
const at = (path: string, make: () => Response | Promise<Response>, method?: string): Handler => (url, init) =>
  url === `/api${path}` && (!method || (init?.method ?? "GET") === method) ? make() : undefined;

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

describe("My tickets states", () => {
  it("shows loading, then the empty state", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    mockFetch(at("/tickets", async () => { await gate; return json({ tickets: [] }); }));
    render(<TicketsPage />);
    expect(screen.getByTestId("loading")).toBeInTheDocument();
    release();
    expect(await screen.findByTestId("empty")).toHaveTextContent("no tickets yet");
    expect(screen.queryByTestId("loading")).toBeNull();
  });

  it("shows an error with Retry that reloads", async () => {
    let n = 0;
    mockFetch(at("/tickets", () => (n++ === 0 ? err(503, "data_unavailable", "Could not load your tickets") : json(fixtureJson("tickets")))));
    render(<TicketsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your tickets");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getAllByTestId("card-ticket").length).toBeGreaterThan(0));
  });
});

async function renderDetail(id: string) {
  await act(async () => { render(<Suspense fallback={null}><HistoryDetail params={Promise.resolve({ id })} /></Suspense>); });
}

describe("History states", () => {
  it("loading → empty", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    mockFetch(at("/conversations", async () => { await gate; return json({ conversations: [] }); }));
    render(<HistoryPage />);
    expect(screen.getByTestId("loading")).toBeInTheDocument();
    release();
    expect(await screen.findByTestId("empty")).toHaveTextContent("No conversations yet");
  });

  it("error + Retry", async () => {
    let n = 0;
    mockFetch(at("/conversations", () => (n++ === 0 ? err(503, "data_unavailable", "history down") : json(fixtureJson("conversations")))));
    render(<HistoryPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("history down");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Guangzhou to Lima")).toBeInTheDocument();
  });

  it("detail: loading, then not found message", async () => {
    mockFetch(at("/conversations/c_x", () => err(404, "not_found", "nope")));
    await renderDetail("c_x");
    expect(await screen.findByRole("alert")).toHaveTextContent("This conversation was not found.");
  });

  it("detail: renders the conversation read-only", async () => {
    mockFetch(at("/conversations/c_01", () => json(conversation())));
    await renderDetail("c_01");
    expect(await screen.findByText("I want to fly from Guangzhou to Lima")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
  });
});

describe("Chat states", () => {
  const summary = fixtureJson("conversations").conversations[0];
  const future = () => new Date(Date.now() + 10 * 60_000).toISOString();
  const withFutureExpiry = (msgs: any[]) => msgs.map((m) => ({ ...m, cards: m.cards.map((c: any) => (c.expires_at ? { ...c, expires_at: future() } : c)) }));
  const convRoutes = (msgs = withFutureExpiry(conversation().messages)): Handler[] => [
    at("/conversations", () => json({ conversations: [summary] }), "GET"),
    at(`/conversations/${summary.id}`, () => json({ ...conversation(), messages: msgs })),
  ];

  it("loading → empty conversation hint; list error has Retry", async () => {
    mockFetch(at("/conversations", () => json({ conversations: [] })));
    const { unmount } = render(<ChatPage />);
    expect(await screen.findByTestId("empty")).toHaveTextContent("Where would you like to fly?");
    unmount();
    let n = 0;
    mockFetch(at("/conversations", () => (n++ === 0 ? err(503, "data_unavailable", "list down") : json({ conversations: [] }))));
    render(<ChatPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("list down");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("empty")).toBeInTheDocument();
  });

  it("conversation load error shows Retry", async () => {
    let n = 0;
    mockFetch(
      at("/conversations", () => json({ conversations: [summary] }), "GET"),
      at(`/conversations/${summary.id}`, () => (n++ === 0 ? err(503, "data_unavailable", "conv down") : json(conversation()))),
    );
    render(<ChatPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("conv down");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("I want to fly from Guangzhou to Lima")).toBeInTheDocument();
  });

  for (const mode of ["http", "sse"] as const) {
    it(`conversation_busy (${mode}) keeps the input text and does not reload`, async () => {
      const f = mockFetch(
        at(`/conversations/${summary.id}/messages`, () => mode === "http"
          ? err(409, "conversation_busy")
          : new Response('event: error\ndata: {"code":"conversation_busy","message":"busy"}\n\n', { status: 200 })),
        ...convRoutes([]),
      );
      render(<ChatPage />);
      await screen.findByTestId("empty");
      const input = screen.getByLabelText("Message");
      await userEvent.type(input, "hello there");
      await userEvent.click(screen.getByRole("button", { name: "Send" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("still being written");
      expect(input).toHaveValue("hello there");
      const convGets = f.mock.calls.filter(([u]) => u === `/api/conversations/${summary.id}`).length;
      expect(convGets).toBe(1); // initial load only
      expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    });
  }

  it("network drop mid-stream reloads the saved conversation", async () => {
    let loads = 0;
    let pulls = 0;
    const broken = new ReadableStream({
      async pull(c) {
        if (pulls++ === 0) c.enqueue(new TextEncoder().encode('event: user_message\ndata: {"id":"m_x","role":"user","text":"hi","cards":[],"created_at":"2026-11-23T08:00:00Z"}\n\n'));
        else { await new Promise((r) => setTimeout(r, 20)); c.error(new TypeError("network error")); }
      },
    });
    mockFetch(
      at(`/conversations/${summary.id}/messages`, () => new Response(broken, { status: 200 })),
      at("/conversations", () => json({ conversations: [summary] }), "GET"),
      at(`/conversations/${summary.id}`, () => { loads++; return json(conversation()); }),
    );
    render(<ChatPage />);
    await screen.findByText("I want to fly from Guangzhou to Lima");
    await userEvent.type(screen.getByLabelText("Message"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
    expect(loads).toBe(2);
    expect((screen.getByLabelText("Message") as HTMLInputElement).value).toBe(""); // server stored it (user_message seen)
  });

  it("a drop before the server stored the message keeps the input text", async () => {
    mockFetch(
      at(`/conversations/${summary.id}/messages`, () => { throw new TypeError("Failed to fetch"); }),
      ...convRoutes(),
    );
    render(<ChatPage />);
    await screen.findByText("I want to fly from Guangzhou to Lima");
    await userEvent.type(screen.getByLabelText("Message"), "keep me");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
    expect(screen.getByLabelText("Message")).toHaveValue("keep me");
  });

  it("other SSE errors survive the reload", async () => {
    mockFetch(
      at(`/conversations/${summary.id}/messages`, () => new Response(
        'event: user_message\ndata: {"id":"m_x","role":"user","text":"hi","cards":[],"created_at":"2026-11-23T08:00:00Z"}\n\nevent: error\ndata: {"code":"llm_error","message":"The assistant is unavailable."}\n\n', { status: 200 })),
      ...convRoutes(),
    );
    render(<ChatPage />);
    await screen.findByText("I want to fly from Guangzhou to Lima");
    await userEvent.type(screen.getByLabelText("Message"), "hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The assistant is unavailable.");
  });

  it("a stale booking reloads the conversation and the card follows the server state", async () => {
    let stale = false;
    const msgs = () => withFutureExpiry(conversation().messages).map((m: any) => ({ ...m, cards: m.cards.map((c: any) =>
      stale && c.state?.status === "presented" ? { ...c, state: { status: "stale", bookable: false, nonce: null, ticket_id: null } } : c) }));
    mockFetch(
      at(`/quotes/${conversation().messages.at(-1).cards[0].id}/book`, () => { stale = true; return err(409, "stale"); }, "POST"),
      at("/conversations", () => json({ conversations: [summary] }), "GET"),
      at(`/conversations/${summary.id}`, () => json({ ...conversation(), messages: msgs() })),
    );
    render(<ChatPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByText("Out of date")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
    expect(screen.queryByTestId("countdown")).toBeNull();
    expect(screen.getByRole("button", { name: "Get a new quote" })).toBeInTheDocument();
  });

  it("'Get a new quote' on a stale booking sends a chat message", async () => {
    const presented = conversation().messages.at(-1);
    const sent: string[] = [];
    mockFetch(
      at(`/quotes/${presented.cards[0].id}/book`, () => err(409, "stale"), "POST"),
      (url, init) => url === `/api/conversations/${summary.id}/messages` ? (sent.push(JSON.parse(String(init?.body)).text), new Response("", { status: 200 })) : undefined,
      ...convRoutes(),
    );
    render(<ChatPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm booking" }));
    await userEvent.click(await screen.findByRole("button", { name: "Get a new quote" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toContain(presented.cards[0].origin);
    expect(sent[0]).toContain(presented.cards[0].destination);
  });
});

describe("booking error codes (contract)", () => {
  const bookable = () => {
    const c = structuredClone(cardOf("quote"));
    c.state = { status: "presented", bookable: true, nonce: "n_1", ticket_id: null };
    c.expires_at = new Date(Date.now() + 5 * 60_000).toISOString();
    return c;
  };
  const book = async (status: number, code: string, onAsk = vi.fn(), onBooked = vi.fn()) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(err(status, code)));
    render(<CardView card={bookable()} onAsk={onAsk} onBooked={onBooked} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
    return { onAsk, onBooked };
  };

  for (const [status, code] of [[409, "stale"], [409, "expired"], [409, "invalid_nonce"]] as const) {
    it(`${code}: explains, reloads and offers a new quote`, async () => {
      const { onAsk, onBooked } = await book(status, code);
      expect(await screen.findByTestId("booking-error")).toBeInTheDocument();
      expect(onBooked).toHaveBeenCalled();
      await userEvent.click(screen.getByRole("button", { name: "Get a new quote" }));
      expect(onAsk).toHaveBeenCalledWith(expect.stringContaining("Guangzhou"));
    });
  }

  it("already_booked: points to My tickets, reloads, no new-quote button", async () => {
    const { onBooked } = await book(409, "already_booked");
    expect(await screen.findByTestId("booking-error")).toHaveTextContent("My tickets");
    expect(onBooked).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Get a new quote" })).toBeNull();
  });

  for (const [status, code, text] of [[409, "conversation_busy", "still being written"], [503, "data_unavailable", "temporarily unavailable"]] as const) {
    it(`${code}: keeps Confirm available for retry, no reload`, async () => {
      const { onBooked } = await book(status, code);
      expect(await screen.findByTestId("booking-error")).toHaveTextContent(text);
      expect(onBooked).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Confirm booking" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Get a new quote" })).toBeNull();
    });
  }

  it("after stale/expired/invalid_nonce/already_booked the card never offers Confirm or a countdown again", async () => {
    for (const code of ["stale", "expired", "invalid_nonce", "already_booked"]) {
      await book(409, code);
      await screen.findByTestId("booking-error");
      expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
      expect(screen.queryByTestId("countdown")).toBeNull();
      cleanup();
    }
  });

  it("server-side stale / expired cards show the badge and 'Get a new quote', no Confirm or countdown", () => {
    for (const [status, badge] of [["stale", "Out of date"], ["expired", "Expired"]] as const) {
      const c = bookable();
      c.state = { status, bookable: false, nonce: null, ticket_id: null };
      render(<CardView card={c} onAsk={() => {}} />);
      expect(screen.getByText(badge)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
      expect(screen.queryByTestId("countdown")).toBeNull();
      expect(screen.getByRole("button", { name: "Get a new quote" })).toBeInTheDocument();
      cleanup();
    }
  });

  it("a network failure while booking is retryable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    render(<CardView card={bookable()} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByTestId("booking-error")).toHaveTextContent("check your connection");
    expect(screen.getByRole("button", { name: "Confirm booking" })).toBeEnabled();
  });
});

describe("card details", () => {
  const base = () => structuredClone(cardOf("quote"));

  it("booked and expired cards hide 'Valid until'; quoted keeps it", () => {
    const c = base();
    c.state = { status: "booked", bookable: false, nonce: null, ticket_id: "t_1" };
    const { rerender } = render(<CardView card={c} />);
    expect(screen.queryByText(/Valid until/)).toBeNull();
    c.state = { status: "expired", bookable: false, nonce: null, ticket_id: null };
    rerender(<CardView card={{ ...c }} />);
    expect(screen.queryByText(/Valid until/)).toBeNull();
    c.state = { status: "quoted", bookable: false, nonce: null, ticket_id: null };
    rerender(<CardView card={{ ...c }} />);
    expect(screen.getByText(/Valid until/)).toBeInTheDocument();
  });

  it("presented card shows a live countdown and locks when it reaches zero", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    vi.setSystemTime(new Date("2026-11-23T08:00:00Z"));
    const c = base();
    c.state = { status: "presented", bookable: true, nonce: "n_1", ticket_id: null };
    c.expires_at = "2026-11-23T08:02:05Z";
    const onAsk = vi.fn();
    render(<CardView card={c} onAsk={onAsk} />);
    expect(screen.getByTestId("countdown")).toHaveTextContent("Expires in 2:05");
    act(() => { vi.advanceTimersByTime(5000); });
    expect(screen.getByTestId("countdown")).toHaveTextContent("Expires in 2:00");
    act(() => { vi.advanceTimersByTime(120_000); });
    expect(screen.queryByTestId("countdown")).toBeNull();
    expect(screen.getByText("This quote has expired.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
    expect(screen.getByRole("button", { name: "Get a new quote" })).toBeInTheDocument();
  });

  it("similar_names shows a clear note", () => {
    const c = base();
    c.similar_names = [{ city: "San José", connected_hubs: ["Sydney", "Tokyo"] }];
    render(<CardView card={c} />);
    expect(screen.getByTestId("similar-names")).toHaveTextContent("Not to be confused with San José (flies to Sydney, Tokyo)");
  });

  it("no note when similar_names is empty", () => {
    render(<CardView card={base()} />);
    expect(screen.queryByTestId("similar-names")).toBeNull();
  });

  it("ticket id badge never wraps", () => {
    render(<CardView card={cardOf("ticket")} />);
    expect(screen.getByText(/^Ticket t_/).className).toContain("whitespace-nowrap");
  });
});


describe("chat drawer (mobile)", () => {
  it("moves focus into the drawer, closes on Escape and returns focus to the opener", async () => {
    mockFetch(at("/conversations", () => json({ conversations: [] })));
    const { default: userEvent } = await import("@testing-library/user-event");
    render(<ChatPage />);
    await screen.findByTestId("empty");
    const opener = screen.getByRole("button", { name: "Open conversations" });
    await userEvent.click(opener);
    const close = screen.getByRole("button", { name: "Close conversations" });
    expect(close).toHaveFocus();
    expect(opener).toHaveAttribute("aria-expanded", "true");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("example prompts fill the input without sending", async () => {
    const f = mockFetch(at("/conversations", () => json({ conversations: [] })));
    const { default: userEvent } = await import("@testing-library/user-event");
    render(<ChatPage />);
    await screen.findByTestId("empty");
    await userEvent.click(screen.getByRole("button", { name: "Can I fly from Abu Dhabi?" }));
    expect(screen.getByLabelText("Message")).toHaveValue("Can I fly from Abu Dhabi?");
    expect(f.mock.calls.some(([u]) => String(u).includes("/messages"))).toBe(false);
  });
});
