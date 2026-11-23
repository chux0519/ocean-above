// Legal pages: the text is docs/iterations/05-launch/legal.md, copied verbatim to src/content/legal.md (the web
// image is built from frontend/ only). tests/legal.test.ts fails when the two copies differ.
// Source markdown wraps lines mid-sentence: paragraphs and list items are joined with spaces, never <br>.

export type LegalBlock = { t: "p"; text: string } | { t: "ul"; items: string[] };
export type LegalSection = { title: string; blocks: LegalBlock[] };

/** Split the document into its `## ` sections (anything before the first one is the file's own preamble). */
export function parseLegal(md: string): LegalSection[] {
  const out: LegalSection[] = [];
  let cur: LegalSection | null = null;
  let block: LegalBlock | null = null;
  for (const raw of md.replace(/\r\n?/g, "\n").split("\n")) {
    const h = /^## (.+)$/.exec(raw);
    if (h) { cur = { title: h[1].trim(), blocks: [] }; out.push(cur); block = null; continue; }
    if (!cur) continue;
    const line = raw.trim();
    if (!line) { block = null; continue; }
    const li = /^[-*] (.*)$/.exec(raw);
    if (li) {
      if (block?.t !== "ul") { block = { t: "ul", items: [] }; cur.blocks.push(block); }
      block.items.push(li[1].trim());
    } else if (block?.t === "ul" && /^\s/.test(raw)) {
      block.items[block.items.length - 1] += " " + line;          // continuation of a list item
    } else if (block?.t === "p") {
      block.text += " " + line;
    } else {
      block = { t: "p", text: line }; cur.blocks.push(block);
    }
  }
  return out;
}

export function legalSection(md: string, title: string): LegalSection {
  const s = parseLegal(md).find((x) => x.title === title);
  if (!s) throw new Error(`legal.md has no "## ${title}" section`);
  return s;
}
