import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CardView } from "@/components/cards";
import { fixtureJson, cardOf } from "./fixtures";

afterEach(() => vi.restoreAllMocks());

const bookable = () => {
  const c = structuredClone(cardOf("quote"));
  c.state = { status: "presented", bookable: true, nonce: "n_3c1d9e", ticket_id: null };
  c.expires_at = new Date(Date.now() + 10 * 60_000).toISOString();
  return c;
};

describe("quote / booking_request card", () => {
  it("shows every leg, transfers, and *_display values verbatim", () => {
    render(<CardView card={bookable()} />);
    expect(screen.getByText("Guangzhou → Frankfurt")).toBeInTheDocument();
    expect(screen.getByText("Amsterdam → Lima")).toBeInTheDocument();
    expect(screen.getByText("1,362.1 km")).toBeInTheDocument();
    expect(screen.getByText("8,928.6 km")).toBeInTheDocument();
    expect(screen.getByText("$1,160.71")).toBeInTheDocument();
    expect(screen.getByText(/Frankfurt, Singapore, Amsterdam/)).toBeInTheDocument();
    // high-precision values are trace-only
    expect(screen.queryByText(/1160\.718/)).toBeNull();
  });

  it("Confirm booking appears only when bookable, and is disabled while streaming", () => {
    const { rerender } = render(<CardView card={bookable()} streaming />);
    expect(screen.getByRole("button", { name: "Confirm booking" })).toBeDisabled();
    rerender(<CardView card={bookable()} />);
    expect(screen.getByRole("button", { name: "Confirm booking" })).toBeEnabled();
    const c = bookable(); c.state.bookable = false; c.state.nonce = null;
    rerender(<CardView card={c} />);
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
  });

  it("no confirm button for booked / expired cards or in read-only history", () => {
    const booked = cardOf("booking_request");
    expect(booked.state.status).toBe("booked");
    render(<CardView card={booked} />);
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
    expect(screen.getByText("Booked")).toBeInTheDocument();
    render(<CardView card={bookable()} readOnly />);
    expect(screen.queryByRole("button", { name: "Confirm booking" })).toBeNull();
  });

  it("clicking Confirm posts the nonce to /api/quotes/{id}/book", async () => {
    const f = vi.fn().mockResolvedValue(new Response(JSON.stringify(fixtureJson("booking_ok")), { status: 200 }));
    vi.stubGlobal("fetch", f);
    const onBooked = vi.fn();
    render(<CardView card={bookable()} onBooked={onBooked} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
    await waitFor(() => expect(onBooked).toHaveBeenCalled());
    expect(f.mock.calls[0][0]).toBe("/api/quotes/q_7f3a/book");
    expect(JSON.parse(f.mock.calls[0][1].body)).toEqual({ nonce: "n_3c1d9e" });
  });

  it("shows an explanation on a stale booking", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(fixtureJson("booking_error_stale")), { status: 409 })));
    render(<CardView card={bookable()} onBooked={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Confirm booking" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Route data changed since this quote");
  });

  it("expands the calculation trace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(fixtureJson("trace")), { status: 200 })));
    render(<CardView card={bookable()} />);
    await userEvent.click(screen.getByRole("button", { name: "Calculation details" }));
    const t = await screen.findByTestId("trace");
    await waitFor(() => expect(t).toHaveTextContent("Formula"));
    expect(t).toHaveTextContent("1362.100000000000000000000000000000");
    expect(t).toHaveTextContent("sha256:0f1e2d");
  });
});

describe("other cards", () => {
  it("ticket", () => {
    render(<CardView card={cardOf("ticket")} />);
    expect(screen.getByTestId("card-ticket")).toHaveTextContent("Guangzhou");
    expect(screen.getByTestId("card-ticket")).toHaveTextContent("$1,160.71");
    expect(screen.getByText("Ticket t_91b2")).toBeInTheDocument();
  });

  it("ticket_list", () => {
    const c = cardOf("ticket_list");
    render(<CardView card={c} />);
    expect(screen.getAllByTestId("card-ticket")).toHaveLength(c.tickets.length);
  });

  it("no_service lists nearest cities with display distances", () => {
    const c = cardOf("no_service");
    render(<CardView card={c} />);
    const el = screen.getByTestId("card-no_service");
    expect(el).toHaveTextContent(c.city);
    for (const n of c.nearest) { expect(el).toHaveTextContent(n.city); expect(el).toHaveTextContent(n.distance_display); }
  });

  it("ambiguous_city lists candidates with hub info", () => {
    const c = cardOf("ambiguous_city");
    render(<CardView card={c} />);
    const el = screen.getByTestId("card-ambiguous_city");
    for (const x of c.candidates) expect(el).toHaveTextContent(x.city);
    expect(el).toHaveTextContent(c.query);
  });

  it("unknown_city shows suggestions", () => {
    const c = cardOf("unknown_city");
    render(<CardView card={c} />);
    expect(screen.getByTestId("card-unknown_city")).toHaveTextContent(c.query);
  });

  it("notice shows the message", () => {
    const c = cardOf("notice");
    render(<CardView card={c} />);
    expect(screen.getByRole("status")).toHaveTextContent(c.message);
  });
});
