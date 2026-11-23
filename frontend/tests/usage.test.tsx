import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtureJson } from "./fixtures";

const router = { replace: vi.fn() }; // stable, like Next's router
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/chat" }));
import { Shell, usageText } from "@/components/shell";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("account and usage in the header", () => {
  it("usage text uses the two numbers from /api/me verbatim", () => {
    const me = fixtureJson("me");
    expect(usageText(me.usage)).toBe(`${me.usage.messages_today} of ${me.usage.daily_limit} messages today`);
  });

  it("header shows the name; the account menu shows email/provider, usage and legal links", async () => {
    const me = { ...fixtureJson("me"), display_name: "Ada", auth_provider: "google", email: "ada@example.com" };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(me))));
    render(<Shell>{() => <p>child</p>}</Shell>);
    const btn = await screen.findByRole("button", { name: "Account: Ada" });
    await userEvent.click(btn);
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();
    expect(screen.getByTestId("usage")).toHaveTextContent("3 of 50 messages today");
    expect(screen.getByRole("link", { name: "Privacy" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("link", { name: "Terms" })).toHaveAttribute("href", "/terms");
  });

  it("invite users (no email) see the sign-in method instead", async () => {
    const me = { ...fixtureJson("me"), auth_provider: "invite", email: null };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(me))));
    render(<Shell>{() => null}</Shell>);
    await userEvent.click(await screen.findByRole("button", { name: /^Account:/ }));
    expect(screen.getByText("Invite code")).toBeInTheDocument();
  });

  it("refreshUser re-reads /api/me (usage after a send)", async () => {
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...fixtureJson("me"), usage: { ...fixtureJson("me").usage, messages_today: n++ === 0 ? 3 : 4 } }))));
    let refresh: () => void = () => {};
    render(<Shell>{({ refreshUser }) => { refresh = refreshUser; return null; }}</Shell>);
    await userEvent.click(await screen.findByRole("button", { name: /^Account:/ }));
    expect(screen.getByTestId("usage")).toHaveTextContent("3 of 50");
    refresh();
    await waitFor(() => expect(screen.getByTestId("usage")).toHaveTextContent("4 of 50"));
  });
});
