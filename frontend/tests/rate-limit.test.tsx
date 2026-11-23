import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtureJson } from "./fixtures";

const refreshUser = vi.fn();
vi.mock("@/components/shell", () => ({
  Shell: ({ children }: { children: (s: unknown) => React.ReactNode }) => <>{children({ user: {}, refreshUser })}</>,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }), usePathname: () => "/chat" }));
import ChatPage from "@/app/chat/page";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function mockApi(limitBody: unknown) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url === "/api/conversations" && (init?.method ?? "GET") === "GET") return json({ conversations: [] });
    if (url === "/api/conversations") return json({ id: "c_1", title: "New conversation", created_at: "", updated_at: "", preview: "" });
    if (url.endsWith("/messages")) return json(limitBody, 429);
    return json({ error: { code: "not_found", message: "nf" } }, 404);
  }));
  return calls;
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); refreshUser.mockReset(); });

describe("429 rate_limited on send", () => {
  it("user limit (contract fixture): says which limit and when it resets, keeps the input, no reload", async () => {
    const body = fixtureJson("rate_limited");
    const calls = mockApi(body);
    render(<ChatPage />);
    const input = await screen.findByLabelText("Message");
    await userEvent.type(input, "Fly me to Lima");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    const notice = await screen.findByTestId("rate-limited");
    expect(notice).toHaveTextContent("You’ve reached today’s message limit");
    expect(notice).toHaveTextContent(body.error.message); // includes the reset time from the server
    expect(input).toHaveValue("Fly me to Lima");
    expect(calls.filter((c) => c.startsWith("GET /api/conversations/"))).toEqual([]); // nothing stored → no reload
    expect(screen.queryByTestId("error")).toBeNull();
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
  });

  it("site budget: different heading, server message, input kept", async () => {
    const msg = "The demo has used today's budget. It resets at 00:00 UTC.";
    mockApi({ error: { code: "rate_limited", message: msg, scope: "site", resets_at: "2026-11-24T00:00:00Z" } });
    render(<ChatPage />);
    const input = await screen.findByLabelText("Message");
    await userEvent.type(input, "hi");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByTestId("rate-limited")).toHaveTextContent("daily budget is used up");
    expect(screen.getByTestId("rate-limited")).toHaveTextContent(msg);
    expect(input).toHaveValue("hi");
  });
});
