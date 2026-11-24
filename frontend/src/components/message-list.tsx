"use client";
import type { Message } from "@/lib/types";
import type { Draft } from "@/lib/chat";
import { Markdown } from "@/components/markdown";
import { CardView } from "@/components/cards";
import { cn } from "@/lib/utils";
import { StopIcon } from "@/components/icons";

/** The marker of a reply the user stopped (contract v1.6): quiet, not an error. */
export function StoppedMarker({ label = "Stopped" }: { label?: string }) {
  return (
    <p className="inline-flex items-center gap-1.5 text-sm text-ink-2" data-testid="stopped-marker">
      <StopIcon size={12} className="fill-current" />{label}
    </p>
  );
}

function Bubble({ m, streaming, readOnly, onBooked, onAsk }: { m: Pick<Message, "role" | "text" | "cards" | "status">; streaming?: boolean; readOnly?: boolean; onBooked?: () => void; onAsk?: (t: string) => void }) {
  const user = m.role === "user";
  const stopped = m.role === "assistant" && m.status === "cancelled";
  return (
    <div className={cn("flex flex-col gap-3", user ? "items-end" : "items-start")}>
      {m.text && (
        <div className={cn("text-md", user ? "max-w-[80%] rounded-2xl bg-sunken px-4 py-2.5" : m.role === "system" ? "inline-flex rounded-full bg-sunken px-3 py-1 text-sm text-ink-2" : "w-full")}>
          {user ? <p className="whitespace-pre-wrap break-words">{m.text}</p> : <Markdown text={m.text} streaming={streaming} />}
        </div>
      )}
      {stopped && m.text && <StoppedMarker />}
      {m.cards.map((c, i) => <CardView key={i} card={c} streaming={streaming} readOnly={readOnly} stopped={stopped} onBooked={onBooked} onAsk={onAsk} />)}
      {stopped && !m.text && <StoppedMarker label="Stopped before a reply" />}
    </div>
  );
}

export function MessageList({ messages, draft, readOnly, onBooked, onAsk }: { messages: Message[]; draft?: Draft | null; readOnly?: boolean; onBooked?: () => void; onAsk?: (t: string) => void }) {
  return (
    <div className="mx-auto max-w-3xl space-y-8" data-testid="messages">
      {messages.map((m) => <Bubble key={m.id} m={m} readOnly={readOnly} onBooked={onBooked} onAsk={onAsk} />)}
      {draft && (
        <>
          <Bubble m={{ role: "assistant", text: draft.text, cards: draft.cards }} streaming readOnly={readOnly} />
          {draft.status && !draft.text && <p className="flex items-center gap-2 text-sm text-ink-2" role="status"><span className="typing inline-flex gap-1" aria-hidden><span className="size-1.5 rounded-full bg-ink-3" /><span className="size-1.5 rounded-full bg-ink-3" /><span className="size-1.5 rounded-full bg-ink-3" /></span>{draft.status}</p>}
          {draft.error && <p className="text-sm text-danger" role="alert">{draft.error}</p>}
        </>
      )}
    </div>
  );
}
