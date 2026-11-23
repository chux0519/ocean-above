"use client";
import { Fragment, memo, type ReactNode } from "react";
import { parseBlocks, parseInline, type Block, type Inline } from "@/lib/markdown";

function renderInline(nodes: Inline[]): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text": return <Fragment key={i}>{n.v}</Fragment>;
      case "br": return <br key={i} />;
      case "b": return <strong key={i}>{renderInline(n.c)}</strong>;
      case "i": return <em key={i}>{renderInline(n.c)}</em>;
      case "code": return <code key={i} className="rounded-sm bg-rail px-1 text-[0.9em]">{n.v}</code>;
      case "a": return <a key={i} href={n.href} target="_blank" rel="noopener noreferrer" className="text-primary underline">{renderInline(n.c)}</a>;
    }
  });
}

/** One block; memoised on its source so a streaming delta only re-renders the last block. */
const BlockView = memo(function BlockView({ src }: { src: string }) {
  const b = parseBlocks(src)[0] as Block | undefined;
  if (!b) return null;
  if (b.t === "p") {
    return <p>{b.lines.map((l, i) => <Fragment key={i}>{i > 0 && <br />}{renderInline(parseInline(l))}</Fragment>)}</p>;
  }
  const List = b.t === "ul" ? "ul" : "ol";
  return <List>{b.items.map((it, i) => <li key={i}>{renderInline(parseInline(it))}</li>)}</List>;
});

/** Split the source into block-sized chunks (same boundaries as parseBlocks). */
export function blockSources(text: string): string[] {
  const out: string[] = [];
  let cur: string[] = [], kind = "";
  const push = () => { if (cur.length) out.push(cur.join("\n")); cur = []; kind = ""; };
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    if (!line.trim()) { push(); continue; }
    const k = /^\s{0,3}[-*+]\s+/.test(line) ? "ul" : /^\s{0,3}\d{1,9}[.)]\s+/.test(line) ? "ol" : "p";
    if (cur.length && (k === "p" ? kind !== "p" : kind !== k)) push();
    kind = k; cur.push(line);
  }
  push();
  return out;
}

export function Markdown({ text }: { text: string; streaming?: boolean }) {
  return (
    <div className="max-w-none break-words [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_strong]:font-semibold">
      {blockSources(text).map((src, i) => <BlockView key={i} src={src} />)}
    </div>
  );
}
