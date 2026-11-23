import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fixtureJson } from "./fixtures";
import type { AppConfig } from "@/lib/types";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }), usePathname: () => "/login" }));

import LoginPage from "@/app/login/page";
import { LoginOptions } from "@/components/login-options";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const err = (status: number, code: string, message: string) => json({ error: { code, message } }, status);
const cfg = (over: Partial<AppConfig> = {}): AppConfig => ({ dev_login: false, invite_login: false, google_client_id: null, ...over });

/** Fake GIS: records initialize/renderButton; `signIn(token)` plays the popup returning a credential. */
function fakeGis() {
  let cb: ((r: { credential?: string }) => void) | null = null;
  const initialize = vi.fn((o: { callback: (r: { credential?: string }) => void }) => { cb = o.callback; });
  const renderButton = vi.fn((el: HTMLElement) => { el.innerHTML = "<div role='button'>Continue with Google</div>"; });
  window.google = { accounts: { id: { initialize, renderButton } } };
  return { initialize, renderButton, signIn: (credential: string) => cb?.({ credential }) };
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); delete window.google; replace.mockReset(); });

describe("login variants from /api/config", () => {
  it("contract fixture (dev + invite + google): all three, dev form present", async () => {
    const gis = fakeGis();
    vi.stubGlobal("fetch", vi.fn(async () => json(fixtureJson("config"))));
    render(<LoginPage />);
    expect(await screen.findByLabelText("Invite code")).toBeInTheDocument();
    expect(screen.getByLabelText("Your name")).toHaveAttribute("maxLength", "40");
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    await waitFor(() => expect(gis.renderButton).toHaveBeenCalled());
    expect(gis.initialize.mock.calls[0][0]).toMatchObject({ client_id: fixtureJson("config").google_client_id });
    expect(screen.getAllByRole("link", { name: "Privacy Policy" })[0]).toHaveAttribute("href", "/privacy");
    expect(screen.getAllByRole("link", { name: "Terms of Service" })[0]).toHaveAttribute("href", "/terms");
  });

  it("production shape: google + invite, no dev form", () => {
    fakeGis();
    render(<LoginOptions config={cfg({ invite_login: true, google_client_id: "x.apps.googleusercontent.com" })} onDone={() => {}} />);
    expect(screen.getByLabelText("Invite code")).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).toBeNull();
    expect(screen.getByTestId("google-button")).toBeInTheDocument();
  });

  it("local default: dev only, no Google script requested", () => {
    render(<LoginOptions config={cfg({ dev_login: true })} onDone={() => {}} />);
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.queryByLabelText("Invite code")).toBeNull();
    expect(screen.queryByTestId("google-button")).toBeNull();
    expect(document.querySelector('script[src*="accounts.google.com"]')).toBeNull();
  });

  it("nothing enabled: says sign-in is unavailable", () => {
    render(<LoginOptions config={cfg()} onDone={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("not available");
  });

  it("config failure shows an error with Retry", async () => {
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async () => (n++ === 0 ? err(503, "error", "Could not load config") : json(cfg({ invite_login: true })))));
    render(<LoginPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load config");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByLabelText("Invite code")).toBeInTheDocument();
  });
});

describe("sign-in submissions", () => {
  it("invite: posts code + trimmed name, then goes to /chat", async () => {
    const f = vi.fn(async () => json({ id: "u_i", display_name: "Ada", auth_provider: "invite", email: null }));
    vi.stubGlobal("fetch", f);
    const done = vi.fn();
    render(<LoginOptions config={cfg({ invite_login: true })} onDone={done} />);
    await userEvent.type(screen.getByLabelText("Invite code"), "example-code");
    await userEvent.type(screen.getByLabelText("Your name"), "  Ada ");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth/invite");
    expect(JSON.parse(String(init.body))).toEqual({ code: "example-code", name: "Ada" });
  });

  it("invite: 401 shows the server message inline and keeps the inputs", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => err(401, "invalid_credentials", "That invite code is not valid.")));
    const done = vi.fn();
    render(<LoginOptions config={cfg({ invite_login: true })} onDone={done} />);
    await userEvent.type(screen.getByLabelText("Invite code"), "nope");
    await userEvent.type(screen.getByLabelText("Your name"), "Ada");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That invite code is not valid.");
    expect(screen.getByLabelText("Invite code")).toHaveValue("nope");
    expect(done).not.toHaveBeenCalled();
  });

  it("login throttle (429) is shown inline", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => err(429, "rate_limited", "Too many sign-in attempts. Try again in a minute.")));
    render(<LoginOptions config={cfg({ dev_login: true })} onDone={() => {}} />);
    await userEvent.type(screen.getByLabelText("Username"), "demo");
    await userEvent.type(screen.getByLabelText("Password"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many sign-in attempts");
  });

  it("google: GIS credential is posted to /api/auth/google", async () => {
    const gis = fakeGis();
    const f = vi.fn(async () => json({ id: "u_g", display_name: "Ada", auth_provider: "google", email: "ada@example.com" }));
    vi.stubGlobal("fetch", f);
    const done = vi.fn();
    render(<LoginOptions config={cfg({ google_client_id: "x.apps.googleusercontent.com" })} onDone={done} />);
    await waitFor(() => expect(gis.renderButton).toHaveBeenCalled());
    gis.signIn("header.payload.sig");
    await waitFor(() => expect(done).toHaveBeenCalled());
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth/google");
    expect(JSON.parse(String(init.body))).toEqual({ credential: "header.payload.sig" });
  });

  it("google: backend rejection (401) is shown inline", async () => {
    const gis = fakeGis();
    vi.stubGlobal("fetch", vi.fn(async () => err(401, "invalid_credentials", "Google sign-in could not be verified.")));
    render(<LoginOptions config={cfg({ google_client_id: "x.apps.googleusercontent.com" })} onDone={() => {}} />);
    await waitFor(() => expect(gis.renderButton).toHaveBeenCalled());
    gis.signIn("bad");
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be verified");
  });
});
