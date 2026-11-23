import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageList } from "@/components/message-list";
import { conversation } from "./fixtures";

describe("message list", () => {
  it("renders the whole fixture conversation incl. markdown and every card type", () => {
    render(<MessageList messages={conversation().messages} readOnly />);
    expect(screen.getByText("I want to fly from Guangzhou to Lima")).toBeInTheDocument();
    for (const t of ["quote", "booking_request", "ticket", "ticket_list", "no_service", "ambiguous_city", "unknown_city", "notice"]) {
      expect(screen.getAllByTestId(`card-${t}`).length).toBeGreaterThan(0);
    }
  });

  it("shows status while streaming without text", () => {
    render(<MessageList messages={[]} draft={{ text: "", status: "Looking up routes…", cards: [], error: null }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Looking up routes…");
  });
});
