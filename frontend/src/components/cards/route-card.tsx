"use client";
import { useEffect, useState } from "react";
import type { BookingRequestCard, QuoteCard, TicketCard, Trace } from "@/lib/types";
import { Badge, Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";

type Route = QuoteCard | BookingRequestCard | TicketCard;

const STATUS_LABEL: Record<string, { text: string; tone: "muted" | "ok" | "warn" | "danger" }> = {
  quoted: { text: "Quote", tone: "muted" },
  presented: { text: "Awaiting confirmation", tone: "warn" },
  booked: { text: "Booked", tone: "ok" },
  expired: { text: "Expired", tone: "danger" },
  stale: { text: "Out of date", tone: "danger" },
};

function SimilarNames({ card }: { card: Route }) {
  const names = "similar_names" in card ? card.similar_names ?? [] : [];
  if (!names.length) return null;
  return (
    <p className="mt-2 rounded-md bg-amber-50 p-2 text-sm text-amber-900" data-testid="similar-names">
      {names.map((n, i) => (
        <span key={n.city} className="block">
          {i === 0 ? "Also: " : ""}<strong>{n.city}</strong>
          {n.connected_hubs.length ? <> — flies to {n.connected_hubs.join(", ")}</> : null}
        </span>
      ))}
    </p>
  );
}

function Route({ card }: { card: Route }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-x-2 text-base font-semibold">
        <span>{card.origin}</span><span aria-hidden>→</span><span>{card.destination}</span>
      </div>
      <ol className="mt-3 space-y-1 text-sm" aria-label="Route legs">
        {card.legs.map((l, i) => (
          <li key={i} className="flex flex-wrap justify-between gap-x-3">
            <span>{l.from} → {l.to}</span>
            <span className="text-muted">{l.distance_display}</span>
          </li>
        ))}
      </ol>
      <SimilarNames card={card} />
      <p className="mt-2 text-sm text-muted">
        {card.transfers.length ? <>Transfers: {card.transfers.join(", ")}</> : <>Direct flight</>}
      </p>
      <dl className="mt-3 flex gap-6 border-t border-border pt-3 text-sm">
        <div><dt className="text-muted">Total distance</dt><dd className="font-semibold">{card.distance_display}</dd></div>
        <div><dt className="text-muted">Price</dt><dd className="font-semibold">{card.price_display}</dd></div>
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
    <div className="mt-3">
      <Button variant="ghost" className="px-2" onClick={toggle} aria-expanded={open}>
        {open ? "Hide calculation details" : "Calculation details"}
      </Button>
      {open && (
        <div className="mt-2 space-y-2 rounded-md bg-slate-50 p-3 text-xs" data-testid="trace">
          {err && <p className="text-danger">{err}</p>}
          {!trace && !err && <p className="text-muted">Loading…</p>}
          {trace && (
            <>
              <p><span className="text-muted">Formula:</span> {trace.formula}</p>
              <p><span className="text-muted">Algorithm:</span> {trace.algorithm}</p>
              <ul className="space-y-1">
                {trace.legs.map((l, i) => (
                  <li key={i} className="break-words">
                    {l.from} ({l.from_coord.join(", ")}) → {l.to} ({l.to_coord.join(", ")}): {l.distance}
                    <span className="text-muted"> · {l.source}</span>
                  </li>
                ))}
              </ul>
              <p className="break-all">Distance: {trace.distance}</p>
              <p className="break-all">Price: {trace.price}</p>
              <p className="break-all"><span className="text-muted">Network:</span> {trace.network_fingerprint}</p>
              <p className="text-muted">{trace.note}</p>
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
    <Card data-testid={`card-${card.type}`} className="w-full max-w-md">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <Badge tone={st.tone} className="whitespace-nowrap">{st.text}</Badge>
        {status === "quoted" && <span className="text-xs text-muted">Valid until {new Date(card.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
        {live && !lapsed && <span className="text-xs text-muted" data-testid="countdown">Expires in {formatCountdown(left)}</span>}
      </div>
      <Route card={card} />
      {showConfirm && (
        <Button className="mt-4 w-full" onClick={confirm} disabled={streaming || busy}>
          Confirm booking
        </Button>
      )}
      {lapsed && !err && <p role="status" className="mt-3 text-sm text-danger">This quote has expired.</p>}
      {err && (
        <div role="alert" className="mt-3 text-sm text-danger" data-testid="booking-error">
          <p>{err.text}</p>
        </div>
      )}
      {showNewQuote && (
        <Button className="mt-2 w-full" variant="outline" onClick={() => onAsk!(`Please get me a new quote for ${card.origin} to ${card.destination}.`)} disabled={streaming}>
          Get a new quote
        </Button>
      )}
      <TraceView quoteId={card.id} />
    </Card>
  );
}

export function TicketCardView({ card }: { card: TicketCard }) {
  return (
    <Card data-testid="card-ticket" className="w-full max-w-md border-green-300">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <Badge tone="ok" className="whitespace-nowrap">Ticket {card.id}</Badge>
        <span className="text-xs text-muted">Booked {new Date(card.booked_at).toLocaleString()}</span>
      </div>
      <Route card={card} />
    </Card>
  );
}
