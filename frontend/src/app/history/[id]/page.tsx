"use client";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";
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
    <div className="h-full overflow-y-auto p-3 sm:p-6">
      <Link href="/history" className="inline-flex min-h-11 items-center text-sm text-primary">← All conversations</Link>
      <h1 className="mb-1 text-lg font-semibold">{c?.title}</h1>
      <p className="mb-4 text-xs text-muted">Read-only view</p>
      {!c && !error && <LoadingState label="Loading conversation…" />}
      {error && <ErrorState message={error} onRetry={load} />}
      {c && c.messages.length === 0 && <EmptyState>This conversation has no messages.</EmptyState>}
      {c && <MessageList messages={c.messages} readOnly />}
    </div>
  );
}
export default function HistoryDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Shell>{() => <View id={id} />}</Shell>;
}
