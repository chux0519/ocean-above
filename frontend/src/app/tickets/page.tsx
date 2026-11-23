"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TicketCard } from "@/lib/types";
import { Shell } from "@/components/shell";
import { TicketCardView } from "@/components/cards/route-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

function Tickets() {
  const [tickets, setTickets] = useState<TicketCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null); setTickets(null);
    api.tickets().then(setTickets).catch((e) => setError(e.message ?? "Could not load tickets"));
  }, []);
  useEffect(load, [load]);
  return (
    <div className="h-full overflow-y-auto px-3 py-5 sm:px-6 sm:py-8">
      <div className="mx-auto max-w-3xl">
      <h1 className="text-2xl font-bold tracking-[-0.01em]">My tickets</h1>
      <p className="mt-1 mb-5 text-sm text-ink-2">Every ticket you booked with Ocean Above, newest first.</p>
      {!tickets && !error && <LoadingState label="Loading your tickets…" />}
      {error && <ErrorState message={error} onRetry={load} />}
      {tickets?.length === 0 && <EmptyState>You have no tickets yet. Book a flight in Chat and it will appear here.</EmptyState>}
      <div className="grid gap-4 lg:grid-cols-2 [&>*]:max-w-none">{tickets?.map((t) => <TicketCardView key={t.id} card={t} />)}</div>
      </div>
    </div>
  );
}
export default function TicketsPage() { return <Shell>{() => <Tickets />}</Shell>; }
