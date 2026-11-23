// Minimal markdown subset (no HTML ever): paragraphs, line breaks, **bold**, *italic*, `code`,
// [text](http/https url), one-level ordered / unordered lists. Unclosed markers stay plain text,
// so a half-streamed reply renders as text and turns into formatting once the marker closes.

export type Inline =
  | { t: "text"; v: string }
  | { t: "br" }
  | { t: "b" | "i"; c: Inline[] }
  | { t: "code"; v: string }
  | { t: "a"; href: string; c: Inline[] };

export type Block =
  | { t: "p"; lines: string[] }
  | { t: "ul" | "ol"; items: string[] };

const UL = /^\s{0,3}[-*+]\s+(.*)$/;
const OL = /^\s{0,3}\d{1,9}[.)]\s+(.*)$/;

export function parseBlocks(text: string): Block[] {
  const out: Block[] = [];
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    const last = out[out.length - 1];
    if (!line.trim()) { out.push({ t: "p", lines: [] }); continue; } // blank: separator marker
    const ul = UL.exec(line), ol = OL.exec(line);
    if (ul || ol) {
      const kind = ul ? "ul" : "ol";
      const item = (ul ?? ol)![1];
      if (last && last.t === kind) last.items.push(item); else out.push({ t: kind, items: [item] });
    } else if (last && last.t === "p" && last.lines.length) last.lines.push(line.trim());
    else if (last && last.t === "p" && !last.lines.length) last.lines.push(line.trim());
    else out.push({ t: "p", lines: [line.trim()] });
  }
  return out.filter((b) => b.t !== "p" || b.lines.length);
}

const SAFE_URL = /^https?:\/\/[^\s<>"']+$/i;

export function parseInline(s: string): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ t: "text", v: buf }); buf = ""; } };
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "`") {
      const j = s.indexOf("`", i + 1);
      if (j > i + 1) { flush(); out.push({ t: "code", v: s.slice(i + 1, j) }); i = j + 1; continue; }
    } else if (s.startsWith("**", i)) {
      const j = s.indexOf("**", i + 2);
      if (j > i + 2) { flush(); out.push({ t: "b", c: parseInline(s.slice(i + 2, j)) }); i = j + 2; continue; }
    } else if (ch === "*") {
      let j = s.indexOf("*", i + 1);
      while (j > 0 && s[j + 1] === "*") j = s.indexOf("*", j + 2);
      if (j > i + 1 && s[i + 1] !== " ") { flush(); out.push({ t: "i", c: parseInline(s.slice(i + 1, j)) }); i = j + 1; continue; }
    } else if (ch === "[") {
      const m = /^\[([^\]\n]+)\]\(([^)\s]+)\)/.exec(s.slice(i));
      if (m) {
        flush();
        if (SAFE_URL.test(m[2])) out.push({ t: "a", href: m[2], c: parseInline(m[1]) });
        else out.push({ t: "text", v: m[0] }); // unsafe scheme: show the raw text, no link
        i += m[0].length; continue;
      }
    }
    buf += ch; i++;
  }
  flush();
  return out;
}
