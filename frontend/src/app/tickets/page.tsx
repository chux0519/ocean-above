"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TicketCard } from "@/lib/types";
import { Shell } from "@/components/shell";
import { TicketCardView } from "@/components/cards/route-card";

function Tickets() {
  const [tickets, setTickets] = useState<TicketCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.tickets().then(setTickets).catch((e) => setError(e.message)); }, []);
  return (
    <div className="h-full overflow-y-auto p-3 sm:p-6">
      <h1 className="mb-3 text-lg font-semibold">My tickets</h1>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {tickets?.length === 0 && <p className="text-muted">You have no tickets yet.</p>}
      <div className="space-y-3">{tickets?.map((t) => <TicketCardView key={t.id} card={t} />)}</div>
    </div>
  );
}
export default function TicketsPage() { return <Shell>{() => <Tickets />}</Shell>; }
