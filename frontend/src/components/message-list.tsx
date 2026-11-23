"use client";
import type { Message } from "@/lib/types";
import type { Draft } from "@/lib/chat";
import { Markdown } from "@/components/markdown";
import { CardView } from "@/components/cards";
import { cn } from "@/lib/utils";

function Bubble({ m, streaming, readOnly, onBooked, onAsk }: { m: Pick<Message, "role" | "text" | "cards">; streaming?: boolean; readOnly?: boolean; onBooked?: () => void; onAsk?: (t: string) => void }) {
  const user = m.role === "user";
  return (
    <div className={cn("flex flex-col gap-2", user ? "items-end" : "items-start")}>
      {m.text && (
        <div className={cn("max-w-[85%] text-[0.9375rem] leading-6", user ? "rounded-xl rounded-br-sm bg-accent-tint px-4 py-2" : m.role === "system" ? "rounded-md bg-rail px-3 py-1.5 text-sm text-ink-2" : "max-w-[42rem] px-1")}>
          {user ? <p className="whitespace-pre-wrap break-words">{m.text}</p> : <Markdown text={m.text} streaming={streaming} />}
        </div>
      )}
      {m.cards.map((c, i) => <CardView key={i} card={c} streaming={streaming} readOnly={readOnly} onBooked={onBooked} onAsk={onAsk} />)}
    </div>
  );
}

export function MessageList({ messages, draft, readOnly, onBooked, onAsk }: { messages: Message[]; draft?: Draft | null; readOnly?: boolean; onBooked?: () => void; onAsk?: (t: string) => void }) {
  return (
    <div className="mx-auto max-w-3xl space-y-5" data-testid="messages">
      {messages.map((m) => <Bubble key={m.id} m={m} readOnly={readOnly} onBooked={onBooked} onAsk={onAsk} />)}
      {draft && (
        <>
          <Bubble m={{ role: "assistant", text: draft.text, cards: draft.cards }} streaming readOnly={readOnly} />
          {draft.status && !draft.text && <p className="flex items-center gap-2 px-1 text-sm text-ink-2" role="status"><span className="typing inline-flex gap-1" aria-hidden><span className="size-1.5 rounded-full bg-ink-2" /><span className="size-1.5 rounded-full bg-ink-2" /><span className="size-1.5 rounded-full bg-ink-2" /></span>{draft.status}</p>}
          {draft.error && <p className="text-sm text-danger" role="alert">{draft.error}</p>}
        </>
      )}
    </div>
  );
}
