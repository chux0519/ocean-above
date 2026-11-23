import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { THEME_BOOT } from "@/lib/theme";
import "./globals.css";

// Public Sans from the repo (src/fonts): builds offline, no request to Google at build or run time.
const publicSans = localFont({
  src: "../fonts/PublicSans-Variable.woff2", weight: "100 900", style: "normal",
  variable: "--font-public-sans", display: "swap",
});

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
