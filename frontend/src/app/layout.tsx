import type { Metadata, Viewport } from "next";
import { Public_Sans } from "next/font/google";
import { THEME_BOOT } from "@/lib/theme";
import "./globals.css";

// Downloaded at build time and self-hosted by Next (no runtime request to Google).
const publicSans = Public_Sans({ subsets: ["latin", "latin-ext"], variable: "--font-public-sans", display: "swap" });

export const metadata: Metadata = { title: "Ocean Above", description: "Book flights in plain language" };
export const viewport: Viewport = {
  width: "device-width", initialScale: 1,
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#0e2f4a" }, { media: "(prefers-color-scheme: dark)", color: "#0a2236" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={publicSans.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
