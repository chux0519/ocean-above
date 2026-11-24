/** #55 P1: conversation loads that resolve out of order never replace the chat on screen. Every GET of a conversation
 * is held by the test and released in the order the test chooses (B before A). The check after each scenario: the
 * messages on screen belong to the conversation selected in the sidebar. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { cardOf, fixtureJson } from "./fixtures";
import type { Message } from "@/lib/types";

vi.mock("@/components/shell", () => ({
  Shell: ({ children }: { children: (s: unknown) => React.ReactNode }) =>
    <>{children({ user: {}, refreshUser: () => {}, sidebarSlot: document.body, closeNav: () => {} })}</>,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }), usePathname: () => "/chat" }));
import ChatPage from "@/app/chat/page";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const summary = (id: string, title: string) => ({ id, title, created_at: "", updated_at: "", preview: "" });
const msg = (id: string, role: "user" | "assistant", text: string, cards: Message["cards"] = []): Message =>
  ({ id, role, text, cards, created_at: "2026-11-24T00:00:00Z", status: "completed" }) as Message;

function bookableQuote() {
  const c = structuredClone(cardOf("quote"));
  c.state = { status: "presented", bookable: true, nonce: "n_3c1d9e", ticket_id: null };
  c.expires_at = new Date(Date.now() + 10 * 60_000).toISOString();
  return c;
}

const CONVS: Record<string, { title: string; messages: Message[] }> = {
  c_a: { title: "Alpha trip", messages: [msg("m_a1", "user", "Alpha question"), msg("m_a2", "assistant", "Alpha reply.", [bookableQuote()])] },
  c_b: { title: "Bravo trip", messages: [msg("m_b1", "user", "Bravo question"), msg("m_b2", "assistant", "Bravo reply.")] },
  c_new: { title: "New conversation", messages: [] },
};

/** Fake server: every GET /api/conversations/{id} (and, with `holdList`, the list and POST) waits in a queue until the
 * test releases it. */
function server({ holdList = false, holdCreate = false } = {}) {
  const calls: string[] = [];
  const held: { key: string; go: () => void; fail: () => void }[] = [];
  const hold = (key: string, body: () => Response) => new Promise<Response>((resolve) => {
    held.push({ key, go: () => resolve(body()), fail: () => resolve(json({ error: { code: "internal_error", message: "boom" } }, 500)) });
  });
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url}`);
    if (url === "/api/conversations" && method === "GET") {
      const body = () => json({ conversations: [summary("c_a", CONVS.c_a.title), summary("c_b", CONVS.c_b.title)] });
      return holdList ? hold("list", body) : body();
    }
    if (url === "/api/conversations" && method === "POST") {
      const body = () => json(summary("c_new", "New conversation"));
      return holdCreate ? hold("create", body) : body();
    }
    const m = url.match(/^\/api\/conversations\/(c_[a-z]+)$/);
    if (m) {
      const id = m[1];
      return hold(id, () => json({ ...summary(id, CONVS[id].title), messages: CONVS[id].messages }));
    }
    if (url === "/api/quotes/q_7f3a/book") return json(fixtureJson("booking_ok"));
    return json({ error: { code: "not_found", message: "nf" } }, 404);
  }));
  /** Release the oldest held request for `key` (a conversation id, "list" or "create"). */
  const release = (key: string, ok = true) => act(async () => {
    const i = held.findIndex((h) => h.key === key);
    if (i < 0) throw new Error(`nothing held for ${key}`);
    const [h] = held.splice(i, 1);
    if (ok) h.go(); else h.fail();
    await new Promise((r) => setTimeout(r, 20)); // let every continuation land
  });
  const waitHeld = (key: string, n = 1) => waitFor(() => expect(held.filter((h) => h.key === key).length).toBe(n));
  return { calls, release, waitHeld, held };
}

const sidebar = () => screen.getByRole("list", { name: "Conversations" });
const selected = () => within(sidebar()).queryAllByRole("button").filter((b) => b.getAttribute("aria-current") === "true")
  .map((b) => b.textContent);
const shown = () => {
  const pane = screen.getByTestId("messages");
  return ["Alpha", "Bravo"].filter((name) => within(pane).queryByText(`${name} question`));
};
const click = (title: string) => userEvent.click(within(sidebar()).getByRole("button", { name: new RegExp(title) }));

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("conversation loads out of order (#55)", () => {
  it("rapid selection: A is slow, B is fast — B stays on screen when A lands", async () => {
    const srv = server();
    render(<ChatPage />);
    await srv.waitHeld("c_a"); await srv.release("c_a"); // the first load (init)
    expect(shown()).toEqual(["Alpha"]);
    await click("Bravo"); await click("Alpha"); await click("Bravo"); // A then B, twice; only the last B counts
    await srv.waitHeld("c_b", 2); await srv.waitHeld("c_a", 1);
    expect(shown()).toEqual([]); // while B loads, A's old messages are not shown under B's selection
    await srv.release("c_b"); // the first B (stale: A was selected after it)
    expect(shown()).toEqual([]);
    expect(screen.queryAllByText("Loading conversation…").length).toBeGreaterThan(0);
    await srv.release("c_b"); // the current B
    expect(selected()).toEqual([expect.stringContaining("Bravo")]);
    expect(shown()).toEqual(["Bravo"]);
    expect(screen.queryAllByText("Loading conversation…")).toHaveLength(0);
    await srv.release("c_a"); // A resolves last
    expect(selected()).toEqual([expect.stringContaining("Bravo")]);
    expect(shown()).toEqual(["Bravo"]);
    expect(screen.queryAllByText("Loading conversation…")).toHaveLength(0);
  });

  it("a stale load that fails shows no error over the chat on screen", async () => {
    const srv = server();
    render(<ChatPage />);
    await srv.waitHeld("c_a"); await srv.release("c_a");
    await click("Bravo"); await click("Alpha");
    await srv.waitHeld("c_a");
    await srv.release("c_a");
    await srv.release("c_b", false); // B fails after A is on screen
    expect(shown()).toEqual(["Alpha"]);
    expect(screen.queryByTestId("error")).toBeNull();
  });

  it("initial load vs selection: the first conversation's load lands after the user picked B", async () => {
    const srv = server();
    render(<ChatPage />);
    await srv.waitHeld("c_a"); // init is loading A (the first conversation)
    await click("Bravo");
    await srv.waitHeld("c_b");
    await srv.release("c_b");
    expect(shown()).toEqual(["Bravo"]);
    await srv.release("c_a"); // init's load resolves late
    expect(selected()).toEqual([expect.stringContaining("Bravo")]);
    expect(shown()).toEqual(["Bravo"]);
    expect(screen.queryAllByText("Loading conversation…")).toHaveLength(0);
  });

  it("New chat while the list is still loading: init does not open the first conversation afterwards", async () => {
    const srv = server({ holdList: true });
    render(<ChatPage />);
    await srv.waitHeld("list");
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(srv.calls).toContain("POST /api/conversations"));
    await srv.release("list");
    expect(srv.calls).not.toContain("GET /api/conversations/c_a");
    expect(selected()).toEqual([expect.stringContaining("New")]);
    expect(shown()).toEqual([]);
  });

  it("New chat created after the user already picked another chat does not take over the view", async () => {
    const srv = server({ holdCreate: true });
    render(<ChatPage />);
    await srv.waitHeld("c_a"); await srv.release("c_a");
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    await srv.waitHeld("create");
    await click("Bravo");
    await srv.waitHeld("c_b"); await srv.release("c_b");
    await srv.release("create");
    expect(selected()).toEqual([expect.stringContaining("Bravo")]);
    expect(shown()).toEqual(["Bravo"]);
  });

  it("New chat while A is still loading: A's late load does not fill the new chat", async () => {
    const srv = server();
    render(<ChatPage />);
    await srv.waitHeld("c_a");
    await userEvent.click(screen.getByRole("button", { name: "New chat" }));
    await waitFor(() => expect(selected()).toEqual([expect.stringContaining("New")]));
    await srv.release("c_a");
    expect(shown()).toEqual([]);
    expect(screen.queryAllByText("Loading conversation…")).toHaveLength(0);
  });

  it("booking refresh: A's refresh after a booking click lands after the user switched to B", async () => {
    const srv = server();
    render(<ChatPage />);
    await srv.waitHeld("c_a"); await srv.release("c_a");
    await userEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
    await srv.waitHeld("c_a"); // the booking refresh of A is pending
    await click("Bravo");
    await srv.waitHeld("c_b"); await srv.release("c_b");
    expect(shown()).toEqual(["Bravo"]);
    await srv.release("c_a");
    expect(selected()).toEqual([expect.stringContaining("Bravo")]);
    expect(shown()).toEqual(["Bravo"]);
  });

  it("booking refresh still updates the chat when the user stayed", async () => {
    const srv = server();
    render(<ChatPage />);
    await srv.waitHeld("c_a"); await srv.release("c_a");
    await userEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
    await srv.waitHeld("c_a");
    await srv.release("c_a");
    expect(srv.calls.filter((c) => c === "GET /api/conversations/c_a")).toHaveLength(2);
    expect(shown()).toEqual(["Alpha"]);
  });
});
