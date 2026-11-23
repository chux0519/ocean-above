import type { AmbiguousCityCard, NoServiceCard, NoticeCard, TicketListCard, UnknownCityCard } from "@/lib/types";
import { Badge, Card } from "@/components/ui/card";
import { AlertIcon } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { TicketCardView } from "./route-card";

/** Unserved city: the headline says so; nearest served cities sit in the same distance column as route legs. */
export function NoServiceCardView({ card }: { card: NoServiceCard }) {
  return (
    <Card data-testid="card-no_service" className="enter w-full max-w-md">
      <Badge tone="warn"><AlertIcon size={14} />No service</Badge>
      <h2 className="mt-3 text-lg font-semibold leading-tight">{card.city} has no flights yet</h2>
      {card.nearest.length > 0 && (
        <>
          <div className="mt-4 flex items-end justify-between border-b border-ink pb-1 text-xs font-semibold uppercase tracking-[0.06em] text-ink-2" aria-hidden>
            <span>Nearest city with service</span><span>Distance</span>
          </div>
          <ul className="figures text-sm" aria-label="Nearest cities with service">
            {card.nearest.map((n, i) => (
              <li key={n.city} className="flex items-baseline justify-between gap-3 border-b border-hairline py-2 last:border-b-0">
                <span className={i === 0 ? "font-semibold" : undefined}>{n.city}</span>
                <span className="whitespace-nowrap">{n.distance_display}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

export function AmbiguousCityCardView({ card }: { card: AmbiguousCityCard }) {
  return (
    <Card data-testid="card-ambiguous_city" className="enter w-full max-w-md">
      <h2 className="text-lg font-semibold leading-tight">Which “{card.query}” do you mean?</h2>
      <ul className="mt-3 divide-y divide-hairline border-y border-hairline text-sm">
        {card.candidates.map((c, i) => (
          <li key={i} className="py-2">
            <div className="flex items-center gap-2"><span className="font-semibold">{c.city}</span>{c.is_hub && <Badge tone="muted">Hub</Badge>}</div>
            {c.connected_hubs.length > 0 && <p className="text-ink-2">Flies to {c.connected_hubs.join(", ")}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function UnknownCityCardView({ card }: { card: UnknownCityCard }) {
  return (
    <Card data-testid="card-unknown_city" className="enter w-full max-w-md">
      <h2 className="font-semibold">We don’t know “{card.query}”</h2>
      {card.suggestions.length > 0 && <p className="mt-1 text-sm text-ink-2">Did you mean {card.suggestions.join(", ")}?</p>}
    </Card>
  );
}

export function NoticeCardView({ card }: { card: NoticeCard }) {
  return (
    <Alert tone="warn" data-testid="card-notice" role="status" className="enter w-full max-w-md rounded-lg px-4 py-3">
      <p>{card.message}</p>
    </Alert>
  );
}

export function TicketListCardView({ card }: { card: TicketListCard }) {
  return (
    <div data-testid="card-ticket_list" className="w-full max-w-md space-y-2">
      {card.tickets.length === 0 && <Card className="text-sm text-ink-2">No tickets.</Card>}
      {card.tickets.map((t) => <TicketCardView key={t.id} card={t} />)}
    </div>
  );
}
