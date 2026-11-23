import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Markdown } from "@/components/markdown";

const html = (t: string) => { const { container, unmount } = render(<Markdown text={t} />); const h = container.firstElementChild!.innerHTML; unmount(); return h; };

describe("minimal markdown", () => {
  it("paragraphs and line breaks", () => {
    expect(html("a\nb\n\nc")).toBe("<p>a<br>b</p><p>c</p>");
  });
  it("bold, italic, inline code", () => {
    expect(html("**b** and *i* and `c`")).toContain("<strong>b</strong>");
    expect(html("**b** and *i* and `c`")).toContain("<em>i</em>");
    expect(html("`x*y*`")).toContain("<code");
    expect(html("`x*y*`")).toContain(">x*y*</code>");
  });
  it("lists, one level, ordered and unordered", () => {
    expect(html("- a\n- b")).toBe("<ul><li>a</li><li>b</li></ul>");
    expect(html("1. a\n2) b")).toBe("<ol><li>a</li><li>b</li></ol>");
    expect(html("intro\n* a\n* b\n\nend")).toBe("<p>intro</p><ul><li>a</li><li>b</li></ul><p>end</p>");
  });
  it("links: http(s) only, noopener noreferrer, new tab", () => {
    const h = html("[site](https://example.com/x?a=1)");
    expect(h).toContain('href="https://example.com/x?a=1"');
    expect(h).toContain('target="_blank"');
    expect(h).toContain('rel="noopener noreferrer"');
  });
  it("unclosed markers stay plain text", () => {
    expect(html("**bo")).toBe("<p>**bo</p>");
    expect(html("*it")).toBe("<p>*it</p>");
    expect(html("`co")).toBe("<p>`co</p>");
    expect(html("[a](http://x")).toBe("<p>[a](http://x</p>");
  });
  it("XSS: raw HTML is escaped, javascript: links are not links", () => {
    const h = html('<script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1)) [y](data:text/html,hi)');
    expect(h).not.toContain("<script");
    expect(h).not.toContain("<img");
    expect(h).not.toContain("<a ");
    expect(h).toContain("&lt;script&gt;");
    const { container } = render(<Markdown text="<b>x</b>" />);
    expect(container.querySelector("b")).toBeNull();
  });
  it("Chinese mixed text", () => {
    expect(html("从**广州**飞到*利马*，共 `8,928.6 km`。")).toBe(
      "<p>从<strong>广州</strong>飞到<em>利马</em>，共 <code class=\"rounded bg-slate-100 px-1 text-[0.9em]\">8,928.6 km</code>。</p>");
  });
  it("char-by-char streaming ends identical to one-shot", () => {
    const text = "Route:\n\n1. **Guangzhou** → *Frankfurt*\n2. [map](https://example.com)\n\n- `a`\n- b\n\n未闭合 **x";
    const { container, rerender } = render(<Markdown text="" />);
    for (let i = 1; i <= text.length; i++) rerender(<Markdown text={text.slice(0, i)} />);
    expect(container.firstElementChild!.innerHTML).toBe(html(text));
  });
  it("markers close later: plain text turns into formatting", () => {
    const { container, rerender } = render(<Markdown text="**bold" />);
    expect(container.querySelector("strong")).toBeNull();
    rerender(<Markdown text="**bold**" />);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
  });
});
