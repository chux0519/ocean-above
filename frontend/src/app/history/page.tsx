"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";
import { Shell } from "@/components/shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

function History() {
  const [items, setItems] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null); setItems(null);
    api.conversations().then(setItems).catch((e) => setError(e.message ?? "Could not load history"));
  }, []);
  useEffect(load, [load]);
  return (
    <div className="h-full overflow-y-auto p-3 sm:p-6">
      <h1 className="mb-3 text-lg font-semibold">Conversation history</h1>
      {!items && !error && <LoadingState label="Loading conversations…" />}
      {error && <ErrorState message={error} onRetry={load} />}
      {items?.length === 0 && <EmptyState>No conversations yet. Start one in Chat.</EmptyState>}
      <ul className="space-y-2">
        {items?.map((c) => (
          <li key={c.id}>
            <Link href={`/history/${c.id}`} className="block min-h-11 rounded-lg border border-border bg-card p-3 hover:bg-slate-50">
              <span className="block font-medium">{c.title || "New chat"}</span>
              <span className="block text-sm text-muted">{c.preview}</span>
              <span className="block text-xs text-muted">{new Date(c.updated_at).toLocaleString()}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
export default function HistoryPage() { return <Shell>{() => <History />}</Shell>; }
