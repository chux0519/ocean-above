export type Limited = { scope: "user" | "site" | null; message: string };

/** 429 rate_limited on send: which limit, the server's message (it states the reset time); nothing was stored. */
export function LimitNotice({ limited }: { limited: Limited }) {
  const title = limited.scope === "site" ? "The demo’s daily budget is used up" : "You’ve reached today’s message limit";
  return (
    <div role="alert" data-testid="rate-limited" className="mx-auto mb-2 max-w-3xl rounded-xl border border-border bg-surface px-4 py-3 text-sm shadow-raised">
      <p className="font-semibold text-ink">{title}</p>
      <p className="text-ink-2">{limited.message} Your message is kept.</p>
    </div>
  );
}
