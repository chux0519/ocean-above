import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { LegalPage } from "@/components/legal-page";

describe("legal pages and e-mail obfuscation proxies", () => {
  it.each(["Privacy Policy", "Terms of Service"] as const)("%s HTML has no literal e-mail address (Cloudflare would rewrite it → hydration #418)", (title) => {
    const html = renderToStaticMarkup(<LegalPage title={title} />);
    expect(html).not.toMatch(/[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
    expect(html.replace(/<[^>]+>/g, "")).toMatch(/[\w.+-]+@[\w-]+\.[a-z]{2,}/i);
  });
});
