import type { Card } from "@/lib/types";
import { QuoteCardView, TicketCardView } from "./route-card";
import { AmbiguousCityCardView, NoServiceCardView, NoticeCardView, TicketListCardView, UnknownCityCardView } from "./simple-cards";

export function CardView({ card, streaming, readOnly, onBooked, onAsk }: { card: Card; streaming?: boolean; readOnly?: boolean; onBooked?: () => void; onAsk?: (text: string) => void }) {
  switch (card.type) {
    case "quote":
    case "booking_request":
      return <QuoteCardView card={card} streaming={streaming} readOnly={readOnly} onBooked={onBooked} onAsk={onAsk} />;
    case "ticket": return <TicketCardView card={card} />;
    case "ticket_list": return <TicketListCardView card={card} />;
    case "no_service": return <NoServiceCardView card={card} />;
    case "ambiguous_city": return <AmbiguousCityCardView card={card} />;
    case "unknown_city": return <UnknownCityCardView card={card} />;
    case "notice": return <NoticeCardView card={card} />;
  }
}
