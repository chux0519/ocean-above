import fs from "node:fs";
import path from "node:path";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import { legalSection } from "@/lib/legal";
import { parseInline, type Inline } from "@/lib/markdown";
import { ThemeToggle } from "@/components/theme-toggle";
import { SiteFooter } from "@/components/site-footer";

const EMAIL = /([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;

/** Plain text, with e-mail addresses split into separate elements. A proxy that rewrites literal addresses
 *  (Cloudflare Email Address Obfuscation) would otherwise change the HTML after render and break hydration (#418). */
function plain(v: string): ReactNode {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of v.matchAll(EMAIL)) {
    out.push(v.slice(last, m.index));
    out.push(<span key={m.index}>{m[1]}<span>@</span>{m[2]}</span>);
    last = m.index + m[0].length;
  }
  out.push(v.slice(last));
  return out;
}

/** Inline markdown from our own legal text: **bold**, *italic* / _italic_ (whole-line, e.g. "Last updated"). */
function inline(nodes: Inline[]): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text": return <Fragment key={i}>{plain(n.v)}</Fragment>;
      case "br": return <br key={i} />;
      case "b": return <strong key={i} className="font-semibold">{inline(n.c)}</strong>;
      case "i": return <em key={i}>{inline(n.c)}</em>;
      case "code": return <code key={i}>{n.v}</code>;
      case "a": return <a key={i} href={n.href} className="text-accent underline underline-offset-2">{inline(n.c)}</a>;
    }
  });
}
const text = (s: string) => inline(parseInline(/^_(.+)_$/.test(s) ? `*${s.slice(1, -1)}*` : s));

/** Read at build time (the page is static); src/content/legal.md mirrors the coordinator's legal.md. */
function load(title: string) {
  const md = fs.readFileSync(path.join(process.cwd(), "src/content/legal.md"), "utf8");
  return legalSection(md, title);
}

export function LegalPage({ title }: { title: "Privacy Policy" | "Terms of Service" }) {
  const s = load(title);
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between pr-2 pl-4 sm:px-6">
        <Link href="/" className="inline-flex min-h-11 items-center text-[0.9375rem] font-semibold tracking-[-0.01em]">Ocean Above</Link>
        <ThemeToggle />
      </header>
      <main className="mx-auto w-full max-w-[65ch] flex-1 px-4 pt-6 pb-10 sm:pt-12">
        <article className="space-y-4 text-md text-ink [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5">
          <h1 className="mb-6 text-2xl font-semibold tracking-[-0.02em]">{s.title}</h1>
          {s.blocks.map((b, i) => b.t === "p"
            ? <p key={i} className={/^_.+_$/.test(b.text) ? "text-sm text-ink-2" : undefined}>{text(b.text)}</p>
            : <ul key={i}>{b.items.map((it, j) => <li key={j}>{text(it)}</li>)}</ul>)}
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
