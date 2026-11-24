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
import { ArrowDownIcon, NewChatIcon, RetryIcon, SendUpIcon, StopIcon } from "@/components/icons";
import { StoppedMarker } from "@/components/message-list";

/** Errors sent before user_message: nothing was stored and the slot is refunded, so the input keeps its text
 * and the user can retry (contract v1.5). internal_error counts only before user_message. */
const BUSY = new Set(["conversation_busy", "server_busy", "internal_error"]);
const NOT_SENT: Record<string, string> = {
  conversation_busy: "A reply is still being written, in this chat or another one. Your message is kept — try again in a moment.",
  server_busy: "The assistant is busy right now. Your message is kept — try again in a moment.",
  internal_error: "Something went wrong and your message was not sent. It is kept below — try again.",
};

/** After Stop, how long the stream may take to deliver its `cancelled` event before the reader is closed anyway
 * (counted from the click, so a request whose headers never arrive is closed too). */
export const STOP_GRACE_MS = 3000;
/** After the reader was closed without a terminal event: ask the server how the run ended, this often and this long
 * (a stopped turn first lets a running tool or database step finish, up to about 30 s). */
export const SETTLE_POLL_MS = 1000;
export const SETTLE_MAX_MS = 35000;
const SETTLED = new Set(["cancelled", "completed", "failed"]);

/** Stop pressed but no cancel acknowledgement and no terminal event arrived (e.g. the response headers with the run id
 * were late): the server may still finish the reply (a closed stream does not cancel a run, contract v1.6). */
export const STOP_UNKNOWN = "Stop may not have reached the server — the reply may still finish. Reload to see it.";

/** The reply being streamed: its conversation, run id (from the response headers), reader and Stop state.
 * `acked`: the cancel endpoint answered for this run, so the server knows about the Stop. */
type Live = { conv: string | null; runId: string | null; abort: AbortController; stop: boolean; cancelSent: boolean;
  acked: boolean; grace: ReturnType<typeof setTimeout> | null };

/** Stop pressed while a new chat was still being created: the message is not sent. */
class StoppedBeforeSend extends Error {}

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
  const [stopping, setStopping] = useState(false);
  const [notSent, setNotSent] = useState(false); // stopped before the server stored the message
  const [stopUnknown, setStopUnknown] = useState(false); // Stop without an acknowledgement: outcome unknown (#55)
  const [announce, setAnnounce] = useState(""); // screen-reader message after a Stop (review P2-5)
  const live = useRef<Live | null>(null);
  const action = useRef<HTMLButtonElement>(null); // the Send / Stop button
  // The view generation (#55): bumped whenever the view moves to another conversation (select, new chat, the first
  // load). Every async write of conversation state — a load, a stream, the reload after it, a booking refresh —
  // captures it when it starts and applies only while it is unchanged, so a late response never replaces the chat on
  // screen, whatever order the responses arrive in.
  const view = useRef(0);
  const bottom = useRef<HTMLDivElement>(null);
  const streaming = state.draft !== null || sending;

  /** Leave the current stream without stopping it (switching conversations): the run finishes on the server and
   * is stored; only this view stops reading it. */
  const detach = useCallback(() => {
    view.current += 1;
    live.current?.abort.abort();
    live.current = null;
    setSending(false); setStopping(false); setNotSent(false); setStopUnknown(false); setAnnounce("");
  }, []);

  /** Show conversation `id`: a new view generation; the previous chat's messages leave the screen at once. */
  const enter = useCallback((id: string | null) => {
    detach();
    setActiveId(id); setState({ messages: [], draft: null }); setError(null); setRetry(null);
    return view.current;
  }, [detach]);

  /** Load conversation `id` into the view of generation `gen`. False (nothing changed) when the view has moved on
   * since `gen` was taken. Throws the request's error only while `gen` is still current. */
  const load = useCallback(async (id: string, gen: number, follow = false) => {
    let c;
    try { c = await api.conversation(id); } catch (e) { if (view.current === gen) throw e; return false; }
    if (view.current !== gen) return false;
    if (follow) stick.current = true;
    setState({ messages: c.messages, draft: null });
    return true;
  }, []);

  const refreshList = useCallback(async () => { setList(await api.conversations()); }, []);

  const init = useCallback(async () => {
    setListState("loading"); setError(null); setRetry(null);
    // a chat the user opens or starts meanwhile (New chat, a first message) wins over the first conversation
    let mine = view.current;
    try {
      const items = await api.conversations();
      // keep a chat created while the list was loading (New chat): the list may predate it
      setList((prev) => [...prev.filter((p) => !items.some((i) => i.id === p.id)), ...items]); setListState("ready");
      if (items[0] && view.current === mine && !live.current) {
        const gen = mine = enter(items[0].id); setLoadingConv(true);
        try { await load(items[0].id, gen, true); } finally { if (view.current === gen) setLoadingConv(false); }
      }
    } catch (e) {
      if (view.current !== mine) return;
      setListState("error");
      setError(e instanceof Error ? e.message : "Could not load your conversations");
      setRetry(() => init);
    }
  }, [load, enter]);

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
    const gen = enter(id);
    closeNav(); setLoadingConv(true);
    try { await load(id, gen, true); } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load this conversation");
      setRetry(() => () => select(id));
    } finally { if (view.current === gen) setLoadingConv(false); }
  }
  async function newChat() {
    const gen = enter(null); // leave the chat on screen (or one still loading) now: nothing selected, empty view
    setLoadingConv(false);
    try {
      const c = await api.createConversation();
      setList((l) => [c, ...l]);
      if (view.current !== gen) return; // the user opened another chat meanwhile: stay there
      enter(c.id); closeNav();
    } catch (e) {
      if (view.current !== gen) return;
      setError(e instanceof Error ? e.message : "Could not start a new chat"); setRetry(() => newChat);
    }
  }
  /** After a booking click: the stored conversation, unless the view moved on or a new reply is streaming (its own
   * reload follows). */
  function refreshAfterBooking() {
    if (!activeId || live.current) return;
    load(activeId, view.current).catch(() => {});
  }

  /** Send a message. `fromInput`: the text came from the input box (cleared once the server stores it). */
  async function submit(t: string, fromInput = false) {
    t = t.trim();
    if (!t || streaming) return;
    setError(null); setRetry(null); setLimited(null); setNotSent(false); setStopUnknown(false); setAnnounce(""); setSending(true); toBottom();
    const mine = view.current;
    const here = () => view.current === mine; // still the conversation this stream belongs to
    const run: Live = { conv: activeId, runId: null, abort: new AbortController(), stop: false, cancelSent: false, acked: false, grace: null };
    live.current = run;
    let id = activeId;
    let stoppedEarly = false;    // `cancelled` before user_message: nothing stored, keep the input
    let stored = false;          // user_message seen: the server kept the text
    let busy: string | false = false; // conversation_busy / server_busy: nothing stored, keep the input
    let limit: Limited | null = null; // 429 rate_limited: nothing stored, keep the input
    let streamError: string | null = null;
    let lost = false;
    let ended = false;           // a terminal event (done / cancelled / error) arrived
    try {
      if (!id) {
        const c = await api.createConversation(); id = c.id;
        if (!here()) return;
        setActiveId(id);
      }
      run.conv = id;
      if (run.stop) throw new StoppedBeforeSend(); // Stop while the chat was being created: do not send at all
      let s = state;
      await api.sendMessage(id, t, (ev) => {
        if (!here()) return; // the user switched conversations: never update another chat's view
        if (ev.event === "error" && BUSY.has(ev.data.code) && !stored) { busy = ev.data.code; return; }
        if (ev.event === "cancelled" && !ev.data.message && !stored) stoppedEarly = true;
        if (ev.event === "done" || ev.event === "cancelled" || ev.event === "error") ended = true;
        if (ev.event === "user_message") { stored = true; if (fromInput) setText((p) => (p.trim() === t ? "" : p)); }
        // after Stop the draft is frozen: only the stored user message and the terminal event still count
        if (run.stop && !["user_message", "cancelled", "done", "error"].includes(ev.event)) return;
        if (ev.event === "error") streamError = ev.data.message;
        s = applyEvent(s, ev); setState(s);
      }, run.abort.signal, (runId) => { run.runId = runId; if (run.stop) void requestCancel(run); });
    } catch (err) {
      if (err instanceof StoppedBeforeSend) stoppedEarly = true;
      else if (run.stop || !here()) { /* closed by Stop or by leaving the conversation: not a network error */ } else if (err instanceof ApiError) {
        if (err.code === "conversation_busy" || err.code === "server_busy") busy = err.code;
        else if (err.status === 429) limit = { scope: err.scope ?? null, message: err.message };
        else streamError = err.message;
      } else lost = true; // network drop
    }
    // Stopped, but the reader was closed before the run ended (a slow tool or database step): wait until the server
    // says the run is over, so the reload shows the stopped reply and the conversation lock is free (review P2-3).
    if (run.stop && !ended && !stoppedEarly && here()) await settle(run, here);
    // Stop without any acknowledgement (no run id in time, or the cancel request failed) and no terminal event: the
    // server may never have heard of the Stop, so say the outcome is unknown, never "Stopped" (#55).
    const unknown = run.stop && !ended && !stoppedEarly && !run.acked;
    if (run.grace) clearTimeout(run.grace);
    if (live.current === run) live.current = null;
    if (!here()) return; // another conversation is on screen; its own state is untouched
    const hadFocus = typeof document !== "undefined" && document.activeElement === action.current;
    setSending(false); setStopping(false);
    if (run.stop && hadFocus) box.current?.focus(); // keyboard users continue in the input (review P2-5)
    if (run.stop) setAnnounce(unknown ? STOP_UNKNOWN : stoppedEarly ? "Stopped. Your message was not sent." : "Reply stopped.");
    if (stoppedEarly) {
      setState((s) => ({ ...s, draft: null }));
      setNotSent(true);
      onUsage?.(); // the day's message slot was given back
      return; // nothing stored: no reload, input untouched
    }
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
    if (id) {
      // not `load`: a reload that lands after the user switched conversations must not replace that view (review P2-6)
      const cid = id;
      await api.conversation(cid)
        .then((c) => { if (here()) setState({ messages: c.messages, draft: null }); })
        .catch(() => { reloaded = false; if (here()) setState((s) => ({ ...s, draft: null })); });
    }
    if (!here()) return;
    if (unknown) setStopUnknown(true);
    if (run.stop) { refreshList().catch(() => {}); onUsage?.(); return; } // a stopped reply is not an error
    if (lost) {
      setError(reloaded ? "Connection lost. Showing the saved conversation." : "Connection lost and the conversation could not be reloaded.");
      if (!reloaded && id) { const cid = id; setRetry(() => () => select(cid)); }
    } else if (streamError) setError(streamError);
    refreshList().catch(() => {});
    onUsage?.();
  }

  /** Ask the server to stop the run; the stream then delivers `cancelled`. */
  async function requestCancel(run: Live) {
    if (!run.conv || !run.runId || run.cancelSent) return; // no run id yet: sent when the headers arrive
    run.cancelSent = true;
    try { await api.cancelRun(run.conv, run.runId); run.acked = true; } catch { /* unknown or ended run: the reload shows the truth */ }
  }

  /** Poll the cancel endpoint (idempotent) until the run has ended, the run is unknown, or SETTLE_MAX_MS passed. */
  async function settle(run: Live, here: () => boolean) {
    if (!run.conv || !run.runId) return; // the headers never arrived: nothing to ask about
    const until = Date.now() + SETTLE_MAX_MS;
    while (here() && Date.now() < until) {
      try {
        const { status } = await api.cancelRun(run.conv, run.runId);
        run.acked = true;
        if (SETTLED.has(status)) return;
      } catch { return; } // 404 (forgotten) or offline: the reload shows what is stored
      await new Promise((r) => setTimeout(r, SETTLE_POLL_MS));
    }
  }

  /** Stop: the view stops updating at once; the server stops the run (no further model or tool work). The reader is
   * closed after STOP_GRACE_MS from the click whatever happens, so Stop never hangs (review P2-4). */
  function stop() {
    const run = live.current;
    if (!run || run.stop) return;
    run.stop = true; setStopping(true);
    setState((s) => (s.draft ? { ...s, draft: { ...s.draft, status: null } } : s));
    run.grace = setTimeout(() => run.abort.abort(), STOP_GRACE_MS);
    void requestCancel(run);
  }

  function send(e: React.FormEvent) { e.preventDefault(); void submit(text, true); }
  function onKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Escape" && streaming) { e.preventDefault(); stop(); return; } // Esc stops, like the button
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
        <MessageList messages={state.messages} draft={state.draft} onBooked={refreshAfterBooking} onAsk={(t) => void submit(t)} />
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
          {/* One element for Send and Stop, so focus stays on it when it changes (review P2-5). While stopping it is
              aria-disabled rather than disabled: a disabled button would drop the focus. */}
          <Button ref={action} type={streaming ? "button" : "submit"} aria-label={streaming ? (stopping ? "Stopping" : "Stop") : "Send"}
            title={streaming ? "Stop (Esc)" : undefined} onClick={streaming ? stop : undefined}
            aria-disabled={streaming && stopping ? true : undefined} disabled={!streaming && !text.trim()}
            className={cn("size-11 shrink-0 px-0", streaming && stopping && "opacity-50")}>
            {streaming ? <StopIcon size={14} className="fill-current" /> : <SendUpIcon size={20} strokeWidth={2} />}
          </Button>
        </div>
        <p className="sr-only" aria-live="polite" data-testid="stop-announce">{stopping ? "Stopping the reply…" : announce}</p>
        {notSent && <div className="mx-auto mt-2 flex max-w-3xl justify-center" role="status"><StoppedMarker label="Stopped — your message was not sent" /></div>}
        {stopUnknown && (
          <div className="mx-auto mt-2 flex max-w-3xl flex-wrap items-center justify-center gap-x-2 text-sm text-ink-2" role="status" data-testid="stop-unknown">
            <span className="text-center">{STOP_UNKNOWN}</span>
            {activeId && <Button type="button" variant="ghost" className="min-h-11" onClick={() => { const id = activeId; setStopUnknown(false); void select(id); }}><RetryIcon size={16} />Reload</Button>}
          </div>
        )}
        <p className="mx-auto mt-2 max-w-3xl text-center text-xs text-ink-3">Nothing is booked until you press <span className="font-medium text-ink-2">Confirm booking</span> on a quote.</p>
      </form>
    </section>
  );
}

export default function ChatPage() {
  return <Shell>{({ refreshUser, sidebarSlot, closeNav, headerSlot }) => <Chat onUsage={refreshUser} sidebarSlot={sidebarSlot} closeNav={closeNav} headerSlot={headerSlot} />}</Shell>;
}
