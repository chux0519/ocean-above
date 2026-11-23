"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TicketCard } from "@/lib/types";
import { Shell } from "@/components/shell";
import { TicketCardView } from "@/components/cards/route-card";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/states";
import { TicketIcon } from "@/components/icons";

function Tickets() {
  const [tickets, setTickets] = useState<TicketCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null); setTickets(null);
    api.tickets().then(setTickets).catch((e) => setError(e.message ?? "Could not load tickets"));
  }, []);
  useEffect(load, [load]);
  return (
    <div className="h-full overflow-y-auto px-4 pt-4 pb-10 sm:px-6 sm:pt-10">
      <div className="mx-auto max-w-4xl">
        <PageHeader title="My tickets">Every ticket you booked with Ocean Above, newest first.</PageHeader>
        {!tickets && !error && <LoadingState label="Loading your tickets…" />}
        {error && <ErrorState message={error} onRetry={load} />}
        {tickets?.length === 0 && (
          <EmptyState icon={<TicketIcon size={18} />}
            action={<Link href="/chat" className="inline-flex min-h-11 items-center rounded-full bg-primary px-4 text-sm font-medium text-primary-ink hover:bg-primary-hover">Go to Chat</Link>}>
            You have no tickets yet. Book a flight in Chat and it will appear here.
          </EmptyState>
        )}
        <div className="grid items-start gap-4 lg:grid-cols-2 [&>*]:max-w-none">{tickets?.map((t) => <TicketCardView key={t.id} card={t} />)}</div>
      </div>
    </div>
  );
}
export default function TicketsPage() { return <Shell>{() => <Tickets />}</Shell>; }
