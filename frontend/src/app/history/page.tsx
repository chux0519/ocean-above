"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";
import { ChevronIcon } from "@/components/icons";
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
    <div className="h-full overflow-y-auto px-3 py-5 sm:px-6 sm:py-8">
      <div className="mx-auto max-w-3xl">
      <h1 className="mb-5 text-2xl font-bold tracking-[-0.01em]">Conversation history</h1>
      {!items && !error && <LoadingState label="Loading conversations…" />}
      {error && <ErrorState message={error} onRetry={load} />}
      {items?.length === 0 && <EmptyState>No conversations yet. Start one in Chat.</EmptyState>}
      <ul className="space-y-2">
        {items?.map((c) => (
          <li key={c.id}>
            <Link href={`/history/${c.id}`} className="flex min-h-11 items-center gap-3 rounded-lg border border-hairline bg-surface px-4 py-3 transition-colors duration-150 hover:bg-ground">
              <span className="min-w-0 flex-1"><span className="block font-semibold">{c.title || "New chat"}</span>
              <span className="block text-sm text-ink-2">{c.preview}</span>
              <span className="figures mt-1 block text-xs text-ink-2">{new Date(c.updated_at).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" })}</span></span>
              <ChevronIcon size={18} className="shrink-0 text-ink-2" />
            </Link>
          </li>
        ))}
      </ul>
      </div>
    </div>
  );
}
export default function HistoryPage() { return <Shell>{() => <History />}</Shell>; }
