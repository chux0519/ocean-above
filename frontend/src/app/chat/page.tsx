"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { applyEvent, type ChatState } from "@/lib/chat";
import type { ConversationSummary } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { cn } from "@/lib/utils";

function ConversationList({ items, loading, activeId, onSelect, onNew }: { items: ConversationSummary[]; loading: boolean; activeId: string | null; onSelect: (id: string) => void; onNew: () => void }) {
  return (
    <div className="flex h-full flex-col gap-2 p-2">
      <Button onClick={onNew}>New chat</Button>
      <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto" aria-label="Conversations">
        {loading && <li className="px-3 py-2 text-sm text-muted" role="status">Loading conversations…</li>}
        {!loading && items.length === 0 && <li className="px-3 py-2 text-sm text-muted">No conversations yet.</li>}
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
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [state, setState] = useState<ChatState>({ messages: [], draft: null });
  const [loadingConv, setLoadingConv] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState<(() => void) | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const streaming = state.draft !== null || sending;

  const load = useCallback(async (id: string) => {
    const c = await api.conversation(id);
    setState({ messages: c.messages, draft: null });
  }, []);

  const refreshList = useCallback(async () => { setList(await api.conversations()); }, []);

  const init = useCallback(async () => {
    setListState("loading"); setError(null); setRetry(null);
    try {
      const items = await api.conversations();
      setList(items); setListState("ready");
      if (items[0]) {
        setActiveId(items[0].id); setLoadingConv(true);
        try { await load(items[0].id); } finally { setLoadingConv(false); }
      }
    } catch (e) {
      setListState("error");
      setError(e instanceof Error ? e.message : "Could not load your conversations");
      setRetry(() => init);
    }
  }, [load]);

  useEffect(() => { init(); }, [init]);

  useEffect(() => { bottom.current?.scrollIntoView?.({ block: "end" }); }, [state]);

  async function select(id: string) {
    setDrawer(false); setActiveId(id); setError(null); setRetry(null); setLoadingConv(true);
    try { await load(id); } catch (e) {
      setState({ messages: [], draft: null });
      setError(e instanceof Error ? e.message : "Could not load this conversation");
      setRetry(() => () => select(id));
    } finally { setLoadingConv(false); }
  }
  async function newChat() {
    try {
      const c = await api.createConversation();
      setList((l) => [c, ...l]); setActiveId(c.id); setState({ messages: [], draft: null }); setDrawer(false); setError(null); setRetry(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start a new chat"); setRetry(() => newChat); }
  }

  /** Send a message. `fromInput`: the text came from the input box (cleared once the server stores it). */
  async function submit(t: string, fromInput = false) {
    t = t.trim();
    if (!t || streaming) return;
    setError(null); setRetry(null); setSending(true);
    let id = activeId;
    let stored = false;          // user_message seen: the server kept the text
    let busy = false;            // conversation_busy: nothing stored, keep the input
    let streamError: string | null = null;
    let lost = false;
    try {
      if (!id) { const c = await api.createConversation(); id = c.id; setActiveId(id); }
      let s = state;
      await api.sendMessage(id, t, (ev) => {
        if (ev.event === "error" && ev.data.code === "conversation_busy" && !stored) { busy = true; return; }
        if (ev.event === "user_message") { stored = true; if (fromInput) setText((p) => (p.trim() === t ? "" : p)); }
        if (ev.event === "error") streamError = ev.data.message;
        s = applyEvent(s, ev); setState(s);
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "conversation_busy") busy = true; else streamError = err.message;
      } else lost = true; // network drop
    }
    setSending(false);
    if (busy) {
      setState((s) => ({ ...s, draft: null }));
      setError("A reply is still being written. Your message is kept — try again in a moment.");
      return; // nothing stored: no reload, input untouched
    }
    // Stream over (done, error or disconnect): the server state wins.
    let reloaded = true;
    if (id) await load(id).catch(() => { reloaded = false; setState((s) => ({ ...s, draft: null })); });
    if (lost) {
      setError(reloaded ? "Connection lost. Showing the saved conversation." : "Connection lost and the conversation could not be reloaded.");
      if (!reloaded && id) { const cid = id; setRetry(() => () => select(cid)); }
    } else if (streamError) setError(streamError);
    refreshList().catch(() => {});
  }

  function send(e: React.FormEvent) { e.preventDefault(); void submit(text, true); }

  return (
    <div className="relative flex h-full">
      <aside className="hidden w-72 shrink-0 border-r border-border bg-card md:block">
        <ConversationList items={list} loading={listState === "loading"} activeId={activeId} onSelect={select} onNew={newChat} />
      </aside>
      {drawer && (
        <div className="fixed inset-0 z-20 md:hidden" role="dialog" aria-modal="true" aria-label="Conversations">
          <div className="absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-card shadow-xl">
            <ConversationList items={list} loading={listState === "loading"} activeId={activeId} onSelect={select} onNew={newChat} />
          </div>
        </div>
      )}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-border px-2 md:hidden">
          <Button variant="ghost" onClick={() => setDrawer(true)} aria-label="Open conversations">☰ Conversations</Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-3 sm:p-4">
          {loadingConv && <LoadingState label="Loading conversation…" />}
          {!loadingConv && listState !== "error" && state.messages.length === 0 && !state.draft && !error && (
            <EmptyState>Where would you like to fly? Try “I want to fly from Guangzhou to Lima”.</EmptyState>
          )}
          <MessageList messages={state.messages} draft={state.draft} onBooked={() => activeId && load(activeId).catch(() => {})} onAsk={(t) => void submit(t)} />
          {error && <div className="mt-3"><ErrorState message={error} onRetry={retry ?? undefined} /></div>}
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
