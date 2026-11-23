import fs from "node:fs";
import path from "node:path";
import Link from "next/link";
import { Fragment, type ReactNode } from "react";
import { legalSection } from "@/lib/legal";
import { parseInline, type Inline } from "@/lib/markdown";
import { ThemeToggle } from "@/components/theme-toggle";
import { SiteFooter } from "@/components/site-footer";

/** Inline markdown from our own legal text: **bold**, *italic* / _italic_ (whole-line, e.g. "Last updated"). */
function inline(nodes: Inline[]): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text": return <Fragment key={i}>{n.v}</Fragment>;
      case "br": return <br key={i} />;
      case "b": return <strong key={i} className="font-semibold">{inline(n.c)}</strong>;
      case "i": return <em key={i}>{inline(n.c)}</em>;
      case "code": return <code key={i}>{n.v}</code>;
      case "a": return <a key={i} href={n.href} className="text-primary underline">{inline(n.c)}</a>;
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
      <header className="flex h-14 shrink-0 items-center justify-between bg-shell px-4 text-shell-ink" data-shell>
        <Link href="/" className="inline-flex min-h-11 items-center text-base font-bold tracking-[-0.01em]">Ocean Above</Link>
        <ThemeToggle tone="shell" />
      </header>
      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-8 sm:py-12">
        <article className="space-y-3 text-[15px] leading-relaxed text-ink [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5">
          <h1 className="mb-4 text-2xl font-bold tracking-[-0.01em]">{s.title}</h1>
          {s.blocks.map((b, i) => b.t === "p"
            ? <p key={i} className={/^_.+_$/.test(b.text) ? "text-sm text-ink-2" : undefined}>{text(b.text)}</p>
            : <ul key={i}>{b.items.map((it, j) => <li key={j}>{text(it)}</li>)}</ul>)}
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}
