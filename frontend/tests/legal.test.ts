import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { legalSection, parseLegal } from "@/lib/legal";

const doc = fs.readFileSync(path.resolve(__dirname, "../../docs/iterations/05-launch/legal.md"), "utf8");
const copy = fs.readFileSync(path.resolve(__dirname, "../src/content/legal.md"), "utf8");

describe("legal pages text", () => {
  it("src/content/legal.md is a verbatim copy of docs/iterations/05-launch/legal.md", () => {
    // Fix: cp docs/iterations/05-launch/legal.md frontend/src/content/legal.md
    expect(copy).toBe(doc);
  });

  it("has both sections, no unfilled placeholders", () => {
    expect(parseLegal(copy).map((s) => s.title)).toEqual(["Privacy Policy", "Terms of Service"]);
    expect(copy).not.toMatch(/\{\{/);
  });

  it("joins wrapped lines without losing words", () => {
    const words = (s: string) => s.replace(/[*_\-]/g, " ").split(/\s+/).filter(Boolean);
    for (const title of ["Privacy Policy", "Terms of Service"]) {
      const s = legalSection(copy, title);
      const rendered = s.blocks.flatMap((b) => (b.t === "p" ? [b.text] : b.items)).join(" ");
      const start = copy.indexOf(`## ${title}`) + `## ${title}`.length;
      const next = copy.indexOf("\n## ", start);
      expect(words(rendered)).toEqual(words(copy.slice(start, next < 0 ? undefined : next)));
    }
  });

  it("list items spanning lines stay one item", () => {
    const s = legalSection(copy, "Privacy Policy");
    const items = s.blocks.flatMap((b) => (b.t === "ul" ? b.items : []));
    expect(items.some((i) => i.includes("request no other Google data or permissions"))).toBe(true);
    expect(items.every((i) => !i.includes("\n"))).toBe(true);
  });
});
