"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ConversationSummary } from "@/lib/types";
import { ChevronIcon, HistoryIcon } from "@/components/icons";
import { Shell } from "@/components/shell";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/states";

function History() {
  const [items, setItems] = useState<ConversationSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null); setItems(null);
    api.conversations().then(setItems).catch((e) => setError(e.message ?? "Could not load history"));
  }, []);
  useEffect(load, [load]);
  return (
    <div className="h-full overflow-y-auto px-4 pt-4 pb-10 sm:px-6 sm:pt-10">
      <div className="mx-auto max-w-3xl">
        <PageHeader title="Conversation history">Read any past conversation. Quotes there are read-only.</PageHeader>
        {!items && !error && <LoadingState label="Loading conversations…" />}
        {error && <ErrorState message={error} onRetry={load} />}
        {items?.length === 0 && <EmptyState icon={<HistoryIcon size={18} />}>No conversations yet. Start one in Chat.</EmptyState>}
        {!!items?.length && (
          <ul className="divide-y divide-hairline overflow-hidden rounded-xl border border-border bg-surface shadow-card">
            {items.map((c) => (
              <li key={c.id}>
                <Link href={`/history/${c.id}`} className="flex min-h-11 items-center gap-3 px-4 py-3.5 transition-colors duration-150 hover:bg-hover focus-visible:-outline-offset-2">
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-3">
                      <span className="truncate font-medium">{c.title || "New chat"}</span>
                      <span className="figures shrink-0 text-xs text-ink-3">{new Date(c.updated_at).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" })}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-sm text-ink-2">{c.preview}</span>
                  </span>
                  <ChevronIcon size={16} className="shrink-0 text-ink-3" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
export default function HistoryPage() { return <Shell>{() => <History />}</Shell>; }
