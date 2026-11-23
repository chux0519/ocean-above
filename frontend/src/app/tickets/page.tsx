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
    <div className="h-full overflow-y-auto p-3 sm:p-6">
      <h1 className="mb-3 text-lg font-semibold">My tickets</h1>
      {!tickets && !error && <LoadingState label="Loading your tickets…" />}
      {error && <ErrorState message={error} onRetry={load} />}
      {tickets?.length === 0 && <EmptyState>You have no tickets yet. Book a flight in Chat and it will appear here.</EmptyState>}
      <div className="space-y-3">{tickets?.map((t) => <TicketCardView key={t.id} card={t} />)}</div>
    </div>
  );
}
export default function TicketsPage() { return <Shell>{() => <Tickets />}</Shell>; }
