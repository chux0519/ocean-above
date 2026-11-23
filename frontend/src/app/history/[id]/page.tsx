"use client";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";
import { BackIcon } from "@/components/icons";
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
    <div className="h-full overflow-y-auto px-4 pt-2 pb-10 sm:px-6 sm:pt-6">
      <div className="mx-auto max-w-3xl">
      <Link href="/history" className="-ml-3 inline-flex min-h-11 items-center gap-1 rounded-full px-3 text-sm text-ink-2 transition-colors duration-150 hover:bg-hover hover:text-ink">
        <BackIcon size={16} />All conversations
      </Link>
      <h1 className="mt-2 text-xl font-semibold tracking-[-0.02em]">{c?.title || (c ? "New chat" : "")}</h1>
      <p className="mt-2 mb-8 inline-flex items-center gap-2 rounded-full bg-sunken px-3 py-1 text-sm text-ink-2">Read-only · quotes here cannot be booked; ask again in Chat.</p>
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
