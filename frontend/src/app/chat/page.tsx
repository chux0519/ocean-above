"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { applyEvent, type ChatState } from "@/lib/chat";
import type { ConversationSummary } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function ConversationList({ items, activeId, onSelect, onNew }: { items: ConversationSummary[]; activeId: string | null; onSelect: (id: string) => void; onNew: () => void }) {
  return (
    <div className="flex h-full flex-col gap-2 p-2">
      <Button onClick={onNew}>New chat</Button>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto" aria-label="Conversations">
        {items.map((c) => (
          <li key={c.id}>
            <button onClick={() => onSelect(c.id)}
              className={cn("block min-h-11 w-full rounded-md px-3 py-2 text-left text-sm hover:bg-slate-100", c.id === activeId && "bg-slate-100")}>
              <span className="block truncate font-medium">{c.title || "New chat"}</span>
              <span className="block truncate text-xs text-muted">{c.preview}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Chat() {
  const [list, setList] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [state, setState] = useState<ChatState>({ messages: [], draft: null });
  const [text, setText] = useState("");
  const [drawer, setDrawer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const streaming = state.draft !== null;

  const load = useCallback(async (id: string) => {
    const c = await api.conversation(id);
    setState({ messages: c.messages, draft: null });
  }, []);

  const refreshList = useCallback(async () => { setList(await api.conversations()); }, []);

  useEffect(() => {
    (async () => {
      const items = await api.conversations();
      setList(items);
      if (items[0]) { setActiveId(items[0].id); await load(items[0].id); }
    })().catch((e) => setError(e.message));
  }, [load]);

  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "end" }); }, [state]);

  async function select(id: string) { setDrawer(false); setActiveId(id); setError(null); await load(id); }
  async function newChat() {
    const c = await api.createConversation();
    setList((l) => [c, ...l]); setActiveId(c.id); setState({ messages: [], draft: null }); setDrawer(false);
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || streaming) return;
    setText(""); setError(null);
    let id = activeId;
    try {
      if (!id) { const c = await api.createConversation(); id = c.id; setActiveId(id); }
      let s = state;
      await api.sendMessage(id, t, (ev) => { s = applyEvent(s, ev); setState(s); });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    }
    // Stream over (done, error or disconnect): the server state wins.
    if (id) await load(id).catch(() => setState((s) => ({ ...s, draft: null })));
    refreshList().catch(() => {});
  }

  return (
    <div className="relative flex h-full">
      <aside className="hidden w-72 shrink-0 border-r border-border bg-card md:block">
        <ConversationList items={list} activeId={activeId} onSelect={select} onNew={newChat} />
      </aside>
      {drawer && (
        <div className="fixed inset-0 z-20 md:hidden" role="dialog" aria-modal="true" aria-label="Conversations">
          <div className="absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-card shadow-xl">
            <ConversationList items={list} activeId={activeId} onSelect={select} onNew={newChat} />
          </div>
        </div>
      )}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-border px-2 md:hidden">
          <Button variant="ghost" onClick={() => setDrawer(true)} aria-label="Open conversations">☰ Conversations</Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-4">
          {state.messages.length === 0 && !state.draft && (
            <p className="mt-10 text-center text-sm text-muted">Where would you like to fly? Try “I want to fly from Guangzhou to Lima”.</p>
          )}
          <MessageList messages={state.messages} draft={state.draft} onBooked={() => activeId && load(activeId)} />
          {error && <p role="alert" className="mt-3 text-sm text-danger">{error}</p>}
          <div ref={bottom} />
        </div>
        <form onSubmit={send} className="flex gap-2 border-t border-border bg-card p-2 sm:p-3">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Where do you want to go?" aria-label="Message"
            className="min-h-11 min-w-0 flex-1 rounded-md border border-border px-3 text-base" />
          <Button type="submit" disabled={streaming || !text.trim()}>Send</Button>
        </form>
      </section>
    </div>
  );
}

export default function ChatPage() {
  return <Shell>{() => <Chat />}</Shell>;
}
