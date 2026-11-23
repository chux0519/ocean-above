import type { AmbiguousCityCard, NoServiceCard, NoticeCard, TicketListCard, UnknownCityCard } from "@/lib/types";
import { Badge, Card } from "@/components/ui/card";
import { AlertIcon, SearchIcon } from "@/components/icons";
import { Alert } from "@/components/ui/alert";
import { TicketCardView } from "./route-card";

/** Unserved city (take-home req. 8): an amber "No service" badge, a plain headline, then the nearest served
 * cities with their distances (from the server, `*_display`); the nearest one is marked. */
export function NoServiceCardView({ card }: { card: NoServiceCard }) {
  return (
    <Card data-testid="card-no_service" className="enter w-full max-w-[30rem]">
      <Badge tone="warn"><AlertIcon size={13} />No service</Badge>
      <h2 className="mt-3 text-lg font-semibold tracking-[-0.01em]">{card.city} has no flights yet</h2>
      {card.nearest.length > 0 && (
        <>
          <p className="mt-0.5 text-sm text-ink-2">Nearest cities with service</p>
          <ul className="figures mt-3 space-y-1" aria-label="Nearest cities with service">
            {card.nearest.map((n, i) => (
              <li key={n.city} className={i === 0
                ? "flex items-center justify-between gap-3 rounded-lg bg-sunken px-3 py-2.5"
                : "flex items-center justify-between gap-3 px-3 py-2"}>
                <span className="flex min-w-0 items-center gap-2">
                  <span className={i === 0 ? "font-semibold" : undefined}>{n.city}</span>
                  {i === 0 && <span className="text-xs text-ink-3">Nearest</span>}
                </span>
                <span className="whitespace-nowrap text-sm text-ink-2">{n.distance_display}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

/** Candidates are buttons in a live chat (they send "I mean X."); plain rows when read-only. */
export function AmbiguousCityCardView({ card, onAsk, streaming = false }: { card: AmbiguousCityCard; onAsk?: (text: string) => void; streaming?: boolean }) {
  return (
    <Card data-testid="card-ambiguous_city" className="enter w-full max-w-[30rem]">
      <Badge tone="warn">Which one?</Badge>
      <h2 className="mt-3 text-lg font-semibold tracking-[-0.01em]">Which “{card.query}” do you mean?</h2>
      <p className="mt-0.5 text-sm text-ink-2">{onAsk ? "Pick one, or reply in your own words." : "Reply with the one you mean."}</p>
      <ul className="mt-3 space-y-1.5">
        {card.candidates.map((c, i) => {
          const body = (
            <>
              <span className="flex flex-wrap items-center gap-2"><span className="font-semibold">{c.city}</span>{c.is_hub && <Badge tone="muted">Hub</Badge>}</span>
              {c.connected_hubs.length > 0 && <span className="mt-0.5 block text-sm text-ink-2">Flies to {c.connected_hubs.join(", ")}</span>}
            </>
          );
          return (
            <li key={i}>
              {onAsk
                ? <button type="button" disabled={streaming} onClick={() => onAsk(`I mean ${c.city}.`)}
                    className="block min-h-11 w-full rounded-lg border border-border px-3 py-2.5 text-left transition-colors duration-150 hover:bg-hover disabled:cursor-not-allowed disabled:opacity-60">{body}</button>
                : <div className="rounded-lg border border-hairline px-3 py-2.5">{body}</div>}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

export function UnknownCityCardView({ card }: { card: UnknownCityCard }) {
  return (
    <Card data-testid="card-unknown_city" className="enter flex w-full max-w-[30rem] gap-3">
      <span aria-hidden className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-sunken text-ink-2"><SearchIcon size={16} /></span>
      <div className="min-w-0">
        <h2 className="font-semibold">We don’t know “{card.query}”</h2>
        <p className="mt-0.5 text-sm text-ink-2">
          {card.suggestions.length > 0 ? <>Did you mean {card.suggestions.join(", ")}?</> : <>Check the spelling, or try a nearby larger city.</>}
        </p>
      </div>
    </Card>
  );
}

export function NoticeCardView({ card }: { card: NoticeCard }) {
  return (
    <Alert tone="warn" data-testid="card-notice" role="status" className="enter w-full max-w-[30rem] rounded-xl px-4 py-3">
      <p>{card.message}</p>
    </Alert>
  );
}

export function TicketListCardView({ card }: { card: TicketListCard }) {
  return (
    <div data-testid="card-ticket_list" className="w-full max-w-[30rem] space-y-3">
      {card.tickets.length === 0 && <Card className="text-sm text-ink-2">No tickets yet.</Card>}
      {card.tickets.map((t) => <TicketCardView key={t.id} card={t} />)}
    </div>
  );
}
