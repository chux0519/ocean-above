"use client";
import { useEffect, useState } from "react";
import type { BookingRequestCard, QuoteCard, TicketCard, Trace } from "@/lib/types";
import { Badge, Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { api, ApiError } from "@/lib/api";
import { Alert } from "@/components/ui/alert";
import { ArrowIcon, CheckIcon, ChevronIcon, ClockIcon } from "@/components/icons";

type Route = QuoteCard | BookingRequestCard | TicketCard;

const STATUS_LABEL: Record<string, { text: string; tone: "muted" | "ok" | "warn" | "danger" }> = {
  quoted: { text: "Quote · not booked", tone: "muted" },
  presented: { text: "Awaiting your confirmation", tone: "warn" },
  booked: { text: "Booked", tone: "ok" },
  expired: { text: "Expired", tone: "danger" },
  stale: { text: "Out of date", tone: "danger" },
};

function SimilarNames({ card }: { card: Route }) {
  const names = "similar_names" in card ? card.similar_names ?? [] : [];
  if (!names.length) return null;
  return (
    <Alert tone="warn" className="mt-3" data-testid="similar-names">
      <p>
        {names.map((n, i) => (
          <span key={n.city} className="block">
            {i === 0 ? "Not to be confused with " : "or "}<strong className="font-semibold">{n.city}</strong>
            {n.connected_hubs.length ? <> (flies to {n.connected_hubs.join(", ")})</> : null}
          </span>
        ))}
      </p>
    </Alert>
  );
}

/** One stop on the route line: origin / destination (filled node) or a transfer city (hollow node). */
function Stop({ city, transfer, tone }: { city: string; transfer?: boolean; tone: string }) {
  return (
    <div className="relative flex min-h-7 items-center pl-8">
      <span aria-hidden className={cn("absolute left-[7px] size-[11px] rounded-full",
        transfer ? cn("border-2 bg-surface", tone === "line-dead" ? "border-ink-3" : "border-accent") : tone === "line-dead" ? "bg-ink-3" : "bg-ink")} />
      <span className={cn("min-w-0 break-words", transfer ? "font-medium" : "font-semibold")}>{city}</span>
      {transfer && <span className="ml-2 shrink-0 text-xs text-ink-3">Transfer</span>}
    </div>
  );
}

/** The itinerary: a quiet vertical route line (origin → every transfer city → destination) with each leg's
 * distance on its segment, then the totals. Every leg and transfer city is always shown. */
function Route({ card, dead = false }: { card: Route; dead?: boolean }) {
  const last = card.legs.length - 1;
  const tone = dead ? "line-dead" : "line";
  return (
    <>
      <h2 className="text-lg font-semibold tracking-[-0.01em]">
        {card.origin} <ArrowIcon className="inline-block align-[-3px] text-ink-3" size={18} /> {card.destination}
      </h2>
      <p className="mt-0.5 text-sm text-ink-2">
        {card.transfers.length ? <>Via {card.transfers.join(", ")}</> : <>Direct flight</>}
      </p>
      <SimilarNames card={card} />
      <ol className="figures relative mt-4 text-base" aria-label="Route legs">
        <span aria-hidden className={cn("absolute top-3.5 bottom-3.5 left-3 w-0.5 -translate-x-1/2 rounded-full", dead ? "bg-border" : "bg-accent/35")} />
        {card.legs.map((l, i) => (
          <li key={i}>
            {i === 0 && <Stop city={l.from} tone={tone} />}
            <div className="flex min-h-7 items-center gap-2 pl-8 text-sm text-ink-2">
              <span className="sr-only">{l.from} → {l.to}</span>
              <span aria-hidden className="text-xs text-ink-3">Flight</span>
              <span className="whitespace-nowrap">{l.distance_display}</span>
            </div>
            <Stop city={l.to} transfer={i < last} tone={tone} />
          </li>
        ))}
      </ol>
      <dl className="figures mt-4 space-y-1 border-t border-hairline pt-4">
        <div className="flex items-baseline justify-between gap-3 text-sm">
          <dt className="text-ink-2">Total distance</dt><dd className="font-medium text-ink">{card.distance_display}</dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-sm text-ink-2">Total price</dt><dd className="text-xl font-semibold tracking-[-0.02em]">{card.price_display}</dd>
        </div>
      </dl>
    </>
  );
}

function TraceView({ quoteId }: { quoteId: string }) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !trace) {
      try { setTrace(await api.trace(quoteId)); } catch (e) { setErr(e instanceof Error ? e.message : "Failed to load"); }
    }
  }
  return (
    <div className="mt-2 -mb-2">
      <Button variant="ghost" className="-ml-2 gap-1.5 px-2 font-normal text-ink-2 hover:text-ink" onClick={toggle} aria-expanded={open}>
        <ChevronIcon size={15} className="transition-transform duration-150" style={{ transform: open ? "rotate(90deg)" : "none" }} />
        {open ? "Hide calculation details" : "Calculation details"}
      </Button>
      {open && (
        <div className="figures mt-1 mb-3 space-y-2 rounded-lg bg-sunken p-3 text-xs leading-relaxed text-ink" data-testid="trace">
          {err && <p className="text-danger">{err}</p>}
          {!trace && !err && <p className="text-ink-2">Loading…</p>}
          {trace && (
            <>
              <p><span className="text-ink-2">Formula:</span> {trace.formula}</p>
              <p><span className="text-ink-2">Algorithm:</span> {trace.algorithm}</p>
              <ul className="space-y-1">
                {trace.legs.map((l, i) => (
                  <li key={i} className="break-words">
                    {l.from} ({l.from_coord.join(", ")}) → {l.to} ({l.to_coord.join(", ")}): {l.distance}
                    <span className="text-ink-2"> · {l.source}</span>
                  </li>
                ))}
              </ul>
              <p className="break-all">Distance: {trace.distance}</p>
              <p className="break-all">Price: {trace.price}</p>
              <p className="break-all"><span className="text-ink-2">Network:</span> {trace.network_fingerprint}</p>
              <p className="text-ink-2">{trace.note}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function useNow(active: boolean, step = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), step);
    return () => clearInterval(t);
  }, [active, step]);
  return now;
}

export function formatCountdown(ms: number) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** What to show and offer for each booking error code (contract: errors). */
export function bookingErrorInfo(code: string, serverMessage: string): { text: string; retry: boolean; newQuote: boolean; reload: boolean } {
  switch (code) {
    case "stale":
      return { text: "Route data changed since this quote, so it can no longer be booked.", retry: false, newQuote: true, reload: true };
    case "expired":
      return { text: "This quote has expired.", retry: false, newQuote: true, reload: true };
    case "invalid_nonce":
      return { text: "This confirmation is no longer valid.", retry: false, newQuote: true, reload: true };
    case "already_booked":
      return { text: "This quote is already booked — see My tickets.", retry: false, newQuote: false, reload: true };
    case "conversation_busy":
      return { text: "A reply is still being written. Wait a moment, then try again.", retry: true, newQuote: false, reload: false };
    case "data_unavailable":
      return { text: "Route data is temporarily unavailable. Your quote is unchanged — try again.", retry: true, newQuote: false, reload: false };
    default:
      return { text: serverMessage || "Booking failed.", retry: true, newQuote: false, reload: false };
  }
}

/** Quote / booking_request card. Confirm is shown only when the server says bookable and no stream is running. */
export function QuoteCardView({
  card, streaming = false, readOnly = false, onBooked, onAsk,
}: { card: QuoteCard | BookingRequestCard; streaming?: boolean; readOnly?: boolean; onBooked?: () => void; onAsk?: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ReturnType<typeof bookingErrorInfo> | null>(null);
  const status = card.state.status;
  const dead = !!err?.reload; // the server refused this quote: never offer Confirm / countdown again from stale local state
  const live = status === "presented" && !dead;
  const now = useNow(live);
  const left = new Date(card.expires_at).getTime() - now;
  const lapsed = live && left <= 0;
  const st = lapsed ? STATUS_LABEL.expired : STATUS_LABEL[status] ?? STATUS_LABEL.quoted;
  const showConfirm = card.state.bookable && !!card.state.nonce && !readOnly && !lapsed && !dead;
  const showNewQuote = !readOnly && onAsk && (err?.newQuote || lapsed || status === "stale" || status === "expired");

  async function confirm() {
    if (!card.state.nonce) return;
    setBusy(true); setErr(null);
    try {
      await api.book(card.id, card.state.nonce);
      onBooked?.();
    } catch (e) {
      const info = bookingErrorInfo(e instanceof ApiError ? e.code : "", e instanceof ApiError ? e.message : "Booking failed — check your connection and try again.");
      setErr(info);
      if (info.reload) onBooked?.(); // server state wins: reload
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid={`card-${card.type}`} className={cn("enter w-full max-w-[30rem]", showConfirm && "border-warn/40 shadow-raised")}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <Badge tone={st.tone} className="whitespace-nowrap" aria-live="polite">{st.tone === "ok" && <CheckIcon size={13} />}{st.text}</Badge>
        {status === "quoted" && <span className="figures text-xs text-ink-3">Valid until {new Date(card.expires_at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}</span>}
        {live && !lapsed && <span className="figures inline-flex items-center gap-1 text-xs text-ink-2" data-testid="countdown"><ClockIcon size={13} />Expires in {formatCountdown(left)}</span>}
      </div>
      <Route card={card} dead={dead || lapsed || status === "stale" || status === "expired"} />
      {showConfirm && (
        <Button className="mt-5 min-h-12 w-full text-[0.9375rem]" onClick={confirm} disabled={streaming || busy}>
          Confirm booking
        </Button>
      )}
      {lapsed && !err && <Alert role="status" className="mt-3">This quote has expired.</Alert>}
      {err && (
        <Alert role="alert" className="mt-3" data-testid="booking-error"><p>{err.text}</p></Alert>
      )}
      {showNewQuote && (
        <Button className="mt-3 w-full" variant="outline" onClick={() => onAsk!(`Please get me a new quote for ${card.origin} to ${card.destination}.`)} disabled={streaming}>
          Get a new quote
        </Button>
      )}
      <TraceView quoteId={card.id} />
    </Card>
  );
}

export function TicketCardView({ card }: { card: TicketCard }) {
  return (
    <Card data-testid="card-ticket" className="enter w-full max-w-[30rem]">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <Badge tone="ok" className="whitespace-nowrap"><CheckIcon size={13} />Ticket {card.id}</Badge>
        <span className="figures text-xs text-ink-3">Booked {new Date(card.booked_at).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" })}</span>
      </div>
      <Route card={card} />
    </Card>
  );
}
