import type { AmbiguousCityCard, NoServiceCard, NoticeCard, TicketListCard, UnknownCityCard } from "@/lib/types";
import { Badge, Card } from "@/components/ui/card";
import { TicketCardView } from "./route-card";

export function NoServiceCardView({ card }: { card: NoServiceCard }) {
  return (
    <Card data-testid="card-no_service" className="w-full max-w-md border-amber-300">
      <p className="font-semibold">{card.city} has no flights yet</p>
      {card.nearest.length > 0 && (
        <>
          <p className="mt-2 text-sm text-muted">Nearest cities with service:</p>
          <ul className="mt-1 space-y-1 text-sm">
            {card.nearest.map((n) => (
              <li key={n.city} className="flex justify-between gap-3"><span>{n.city}</span><span className="text-muted">{n.distance_display}</span></li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

export function AmbiguousCityCardView({ card }: { card: AmbiguousCityCard }) {
  return (
    <Card data-testid="card-ambiguous_city" className="w-full max-w-md">
      <p className="font-semibold">Which “{card.query}” do you mean?</p>
      <ul className="mt-2 space-y-2 text-sm">
        {card.candidates.map((c, i) => (
          <li key={i} className="rounded-md bg-slate-50 p-2">
            <div className="flex items-center gap-2"><span className="font-medium">{c.city}</span>{c.is_hub && <Badge tone="ok">Hub</Badge>}</div>
            {c.connected_hubs.length > 0 && <p className="text-muted">Connected hubs: {c.connected_hubs.join(", ")}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function UnknownCityCardView({ card }: { card: UnknownCityCard }) {
  return (
    <Card data-testid="card-unknown_city" className="w-full max-w-md">
      <p className="font-semibold">We don’t know “{card.query}”</p>
      {card.suggestions.length > 0 && <p className="mt-1 text-sm text-muted">Did you mean: {card.suggestions.join(", ")}?</p>}
    </Card>
  );
}

export function NoticeCardView({ card }: { card: NoticeCard }) {
  return (
    <Card data-testid="card-notice" role="status" className="w-full max-w-md border-amber-300 bg-amber-50 text-sm">
      {card.message}
    </Card>
  );
}

export function TicketListCardView({ card }: { card: TicketListCard }) {
  return (
    <div data-testid="card-ticket_list" className="w-full max-w-md space-y-2">
      {card.tickets.length === 0 && <Card className="text-sm text-muted">No tickets.</Card>}
      {card.tickets.map((t) => <TicketCardView key={t.id} card={t} />)}
    </div>
  );
}
