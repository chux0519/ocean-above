"use client";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";
import { ChevronIcon } from "@/components/icons";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";

function View({ id }: { id: string }) {
  const [c, setC] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    setError(null); setC(null);
    api.conversation(id).then(setC).catch((e) => setError(e instanceof ApiError && e.status === 404 ? "This conversation was not found." : e.message ?? "Could not load conversation"));
  }, [id]);
  useEffect(load, [load]);
  return (
    <div className="h-full overflow-y-auto px-3 py-5 sm:px-6 sm:py-8">
      <div className="mx-auto max-w-3xl">
      <Link href="/history" className="-ml-1 inline-flex min-h-11 items-center gap-1 px-1 text-sm font-medium text-accent underline-offset-4 hover:underline">
        <ChevronIcon size={16} className="rotate-180" />All conversations
      </Link>
      <h1 className="mt-1 text-2xl font-bold tracking-[-0.01em]">{c?.title}</h1>
      <p className="mt-1 mb-6 text-sm text-ink-2">Read-only. Quotes here cannot be booked; ask again in Chat.</p>
      {!c && !error && <LoadingState label="Loading conversation…" />}
      {error && <ErrorState message={error} onRetry={load} />}
      {c && c.messages.length === 0 && <EmptyState>This conversation has no messages.</EmptyState>}
      </div>
      {c && <MessageList messages={c.messages} readOnly />}
    </div>
  );
}
export default function HistoryDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Shell>{() => <View id={id} />}</Shell>;
}
