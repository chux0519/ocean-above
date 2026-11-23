"use client";
import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Conversation } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";

function View({ id }: { id: string }) {
  const [c, setC] = useState<Conversation | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.conversation(id).then(setC).catch((e) => setError(e.message)); }, [id]);
  return (
    <div className="h-full overflow-y-auto p-3 sm:p-6">
      <Link href="/history" className="inline-flex min-h-11 items-center text-sm text-primary">← All conversations</Link>
      <h1 className="mb-1 text-lg font-semibold">{c?.title}</h1>
      <p className="mb-4 text-xs text-muted">Read-only view</p>
      {error && <p role="alert" className="text-danger">{error}</p>}
      {c && <MessageList messages={c.messages} readOnly />}
    </div>
  );
}
export default function HistoryDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <Shell>{() => <View id={id} />}</Shell>;
}
