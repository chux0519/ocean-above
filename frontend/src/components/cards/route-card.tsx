"use client";
import { useState } from "react";
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

/** Quote / booking_request card. Confirm is shown only when the server says bookable and no stream is running. */
export function QuoteCardView({
  card, streaming = false, readOnly = false, onBooked,
}: { card: QuoteCard | BookingRequestCard; streaming?: boolean; readOnly?: boolean; onBooked?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const st = STATUS_LABEL[card.state.status] ?? STATUS_LABEL.quoted;
  const showConfirm = card.state.bookable && !!card.state.nonce && !readOnly;

  async function confirm() {
    if (!card.state.nonce) return;
    setBusy(true); setError(null);
    try {
      await api.book(card.id, card.state.nonce);
      onBooked?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Booking failed");
      onBooked?.(); // server state wins: reload
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card data-testid={`card-${card.type}`} className="w-full max-w-md">
      <div className="mb-2 flex items-center justify-between gap-2">
        <Badge tone={st.tone}>{st.text}</Badge>
        <span className="text-xs text-muted">Valid until {new Date(card.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
      <Route card={card} />
      {showConfirm && (
        <Button className="mt-4 w-full" onClick={confirm} disabled={streaming || busy}>
          Confirm booking
        </Button>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}
      <TraceView quoteId={card.id} />
    </Card>
  );
}

export function TicketCardView({ card }: { card: TicketCard }) {
  return (
    <Card data-testid="card-ticket" className="w-full max-w-md border-green-300">
      <div className="mb-2 flex items-center justify-between gap-2">
        <Badge tone="ok">Ticket {card.id}</Badge>
        <span className="text-xs text-muted">Booked {new Date(card.booked_at).toLocaleString()}</span>
      </div>
      <Route card={card} />
    </Card>
  );
}
