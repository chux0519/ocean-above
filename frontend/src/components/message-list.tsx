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
        <div className={cn("max-w-[85%] rounded-2xl px-4 py-2 text-sm", user ? "bg-primary text-primary-fg" : m.role === "system" ? "bg-slate-100 text-muted" : "bg-card border border-border")}>
          {user ? <p className="whitespace-pre-wrap break-words">{m.text}</p> : <Markdown text={m.text} streaming={streaming} />}
        </div>
      )}
      {m.cards.map((c, i) => <CardView key={i} card={c} streaming={streaming} readOnly={readOnly} onBooked={onBooked} onAsk={onAsk} />)}
    </div>
  );
}

export function MessageList({ messages, draft, readOnly, onBooked, onAsk }: { messages: Message[]; draft?: Draft | null; readOnly?: boolean; onBooked?: () => void; onAsk?: (t: string) => void }) {
  return (
    <div className="space-y-4" data-testid="messages">
      {messages.map((m) => <Bubble key={m.id} m={m} readOnly={readOnly} onBooked={onBooked} onAsk={onAsk} />)}
      {draft && (
        <>
          <Bubble m={{ role: "assistant", text: draft.text, cards: draft.cards }} streaming readOnly={readOnly} />
          {draft.status && !draft.text && <p className="text-sm text-muted" role="status">{draft.status}</p>}
          {draft.error && <p className="text-sm text-danger" role="alert">{draft.error}</p>}
        </>
      )}
    </div>
  );
}
