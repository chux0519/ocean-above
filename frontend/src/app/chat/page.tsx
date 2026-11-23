"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, ApiError } from "@/lib/api";
import { applyEvent, type ChatState } from "@/lib/chat";
import type { ConversationSummary } from "@/lib/types";
import { Shell } from "@/components/shell";
import { MessageList } from "@/components/message-list";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { cn } from "@/lib/utils";
import { LimitNotice, type Limited } from "@/components/limit-notice";
import { ArrowDownIcon, NewChatIcon, SendUpIcon } from "@/components/icons";

/** Errors sent before user_message: nothing was stored and the slot is refunded, so the input keeps its text
 * and the user can retry (contract v1.5). internal_error counts only before user_message. */
const BUSY = new Set(["conversation_busy", "server_busy", "internal_error"]);
const NOT_SENT: Record<string, string> = {
  conversation_busy: "A reply is still being written, in this chat or another one. Your message is kept — try again in a moment.",
  server_busy: "The assistant is busy right now. Your message is kept — try again in a moment.",
  internal_error: "Something went wrong and your message was not sent. It is kept below — try again.",
};

const EXAMPLES = ["I want to fly from Guangzhou to Lima", "Can I fly from Abu Dhabi?", "Which tickets do I have?"];

function ConversationList({ items, loading, activeId, onSelect, onNew }: { items: ConversationSummary[]; loading: boolean; activeId: string | null; onSelect: (id: string) => void; onNew: () => void }) {
  return (
    <div className="flex min-h-0 flex-1 flex-col px-2">
      <button type="button" onClick={onNew}
        className="flex min-h-11 items-center gap-3 rounded-md px-3 text-sm text-ink transition-colors duration-150 hover:bg-hover">
        <NewChatIcon size={18} />New chat
      </button>
      <h2 className="px-3 pt-4 pb-1 text-xs font-medium text-ink-3">Recent</h2>
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pb-2" aria-label="Conversations">
        {loading && <li className="px-3 py-2 text-sm text-ink-2" role="status">Loading conversations…</li>}
        {!loading && items.length === 0 && <li className="px-3 py-2 text-sm text-ink-2">No conversations yet.</li>}
        {items.map((c) => (
          <li key={c.id}>
            <button onClick={() => onSelect(c.id)}
              aria-current={c.id === activeId ? "true" : undefined}
              className={cn("block min-h-11 w-full rounded-md px-3 py-1.5 text-left transition-colors duration-150",
                c.id === activeId ? "bg-active" : "hover:bg-hover")}>
              <span className={cn("block truncate text-sm", c.id === activeId ? "font-medium text-ink" : "text-ink")}>{c.title || "New chat"}</span>
              <span className="block truncate text-xs text-ink-3">{c.preview}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Chat({ onUsage, sidebarSlot, closeNav, headerSlot }: { onUsage?: () => void; sidebarSlot: HTMLElement | null; closeNav: () => void; headerSlot?: HTMLElement | null }) {
  const [list, setList] = useState<ConversationSummary[]>([]);
  const [listState, setListState] = useState<"loading" | "ready" | "error">("loading");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [state, setState] = useState<ChatState>({ messages: [], draft: null });
  const [loadingConv, setLoadingConv] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const form = useRef<HTMLFormElement>(null);
  // Grow the composer with its text (up to ~8 lines, then it scrolls).
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 208)}px`;
  }, [text]);
  const [error, setError] = useState<string | null>(null);
  const [limited, setLimited] = useState<Limited | null>(null);
  const [retry, setRetry] = useState<(() => void) | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const streaming = state.draft !== null || sending;

  const load = useCallback(async (id: string, follow = false) => {
    if (follow) stick.current = true;
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
        try { await load(items[0].id, true); } finally { setLoadingConv(false); }
      }
    } catch (e) {
      setListState("error");
      setError(e instanceof Error ? e.message : "Could not load your conversations");
      setRetry(() => init);
    }
  }, [load]);

  useEffect(() => { init(); }, [init]);

  // Follow new content only while the reader is at the bottom; reading further up is never interrupted.
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [away, setAway] = useState(false);
  const onScroll = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 96;
    stick.current = near; setAway(!near);
  }, []);
  const toBottom = useCallback((smooth = false) => {
    stick.current = true; setAway(false);
    const still = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    bottom.current?.scrollIntoView?.({ block: "end", behavior: smooth && !still ? "smooth" : "auto" });
  }, []);
  useEffect(() => { if (stick.current) bottom.current?.scrollIntoView?.({ block: "end" }); }, [state]);

  async function select(id: string) {
    closeNav(); setActiveId(id); setError(null); setRetry(null); setLoadingConv(true);
    try { await load(id, true); } catch (e) {
      setState({ messages: [], draft: null });
      setError(e instanceof Error ? e.message : "Could not load this conversation");
      setRetry(() => () => select(id));
    } finally { setLoadingConv(false); }
  }
  async function newChat() {
    try {
      const c = await api.createConversation();
      setList((l) => [c, ...l]); setActiveId(c.id); setState({ messages: [], draft: null }); closeNav(); setError(null); setRetry(null);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start a new chat"); setRetry(() => newChat); }
  }

  /** Send a message. `fromInput`: the text came from the input box (cleared once the server stores it). */
  async function submit(t: string, fromInput = false) {
    t = t.trim();
    if (!t || streaming) return;
    setError(null); setRetry(null); setLimited(null); setSending(true); toBottom();
    let id = activeId;
    let stored = false;          // user_message seen: the server kept the text
    let busy: string | false = false; // conversation_busy / server_busy: nothing stored, keep the input
    let limit: Limited | null = null; // 429 rate_limited: nothing stored, keep the input
    let streamError: string | null = null;
    let lost = false;
    try {
      if (!id) { const c = await api.createConversation(); id = c.id; setActiveId(id); }
      let s = state;
      await api.sendMessage(id, t, (ev) => {
        if (ev.event === "error" && BUSY.has(ev.data.code) && !stored) { busy = ev.data.code; return; }
        if (ev.event === "user_message") { stored = true; if (fromInput) setText((p) => (p.trim() === t ? "" : p)); }
        if (ev.event === "error") streamError = ev.data.message;
        s = applyEvent(s, ev); setState(s);
      });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "conversation_busy" || err.code === "server_busy") busy = err.code;
        else if (err.status === 429) limit = { scope: err.scope ?? null, message: err.message };
        else streamError = err.message;
      } else lost = true; // network drop
    }
    setSending(false);
    if (limit) {
      setState((s) => ({ ...s, draft: null }));
      setLimited(limit);
      onUsage?.();
      return; // nothing stored: no reload, input untouched
    }
    if (busy) {
      setState((s) => ({ ...s, draft: null }));
      setError(NOT_SENT[busy]);
      // Retry through the form so the fresh render's state is used; card actions resend their own text.
      setRetry(() => () => { if (fromInput) form.current?.requestSubmit(); else void submit(t); });
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
    onUsage?.();
  }

  function send(e: React.FormEvent) { e.preventDefault(); void submit(text, true); }
  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter is a new line; never while an IME is composing (Chinese/Japanese input).
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit(text, true); }
  }

  const conversations = <ConversationList items={list} loading={listState === "loading"} activeId={activeId} onSelect={select} onNew={newChat} />;
  return (
    <section className="flex h-full min-w-0 flex-col" aria-labelledby="chat-title">
      {sidebarSlot && createPortal(conversations, sidebarSlot)}
      {headerSlot && createPortal(<Button variant="ghost" aria-label="New chat" onClick={newChat}><NewChatIcon /></Button>, headerSlot)}
      <h1 id="chat-title" className="sr-only">Chat with Ocean Above</h1>
      <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 pt-4 pb-10 [overflow-anchor:auto] sm:px-6 sm:pt-10">
        {loadingConv && <div className="mx-auto max-w-3xl"><LoadingState label="Loading conversation…" /></div>}
        {!loadingConv && listState !== "error" && state.messages.length === 0 && !state.draft && !error && (
          <EmptyState hero>
            <p className="text-2xl font-semibold tracking-[-0.02em] text-ink">Where would you like to fly?</p>
            <p className="mt-2 text-base">Describe your trip in your own words. You’ll see every leg, the distance and the price before anything is booked.</p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {EXAMPLES.map((ex) => (
                <Button key={ex} variant="outline" className="font-normal" onClick={() => { setText(ex); box.current?.focus(); }}>{ex}</Button>
              ))}
            </div>
          </EmptyState>
        )}
        <MessageList messages={state.messages} draft={state.draft} onBooked={() => activeId && load(activeId).catch(() => {})} onAsk={(t) => void submit(t)} />
        {error && <div className="mx-auto mt-4 max-w-3xl"><ErrorState message={error} onRetry={retry ?? undefined} /></div>}
        <div ref={bottom} />
      </div>
      <form ref={form} onSubmit={send} className="relative shrink-0 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6">
        <div aria-hidden className="pointer-events-none absolute inset-x-0 -top-6 h-6 bg-gradient-to-t from-ground to-transparent" />
        {away && (
          <Button type="button" variant="outline" aria-label="Scroll to latest" onClick={() => toBottom(true)}
            className="pop absolute -top-14 left-1/2 size-11 -translate-x-1/2 px-0 shadow-raised"><ArrowDownIcon size={18} /></Button>
        )}
        {limited && <LimitNotice limited={limited} />}
        <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-3xl border border-border bg-surface p-2 pl-4 shadow-raised transition-[border-color,box-shadow] duration-150 focus-within:border-field focus-within:ring-4 focus-within:ring-accent/10">
          <textarea ref={box} rows={1} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={onKey}
            placeholder="Where do you want to go?" aria-label="Message"
            className="max-h-52 min-h-11 flex-1 resize-none bg-transparent py-2.5 text-md text-ink outline-none focus-visible:outline-none" />
          <Button type="submit" aria-label="Send" className="size-11 shrink-0 px-0" disabled={streaming || !text.trim()}><SendUpIcon size={20} strokeWidth={2} /></Button>
        </div>
        <p className="mx-auto mt-2 max-w-3xl text-center text-xs text-ink-3">Nothing is booked until you press <span className="font-medium text-ink-2">Confirm booking</span> on a quote.</p>
      </form>
    </section>
  );
}

export default function ChatPage() {
  return <Shell>{({ refreshUser, sidebarSlot, closeNav, headerSlot }) => <Chat onUsage={refreshUser} sidebarSlot={sidebarSlot} closeNav={closeNav} headerSlot={headerSlot} />}</Shell>;
}
