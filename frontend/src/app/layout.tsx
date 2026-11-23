import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { THEME_BOOT } from "@/lib/theme";
import "./globals.css";

// Geist from the repo (src/fonts, OFL): builds offline, no font request at build or run time.
const geist = localFont({
  src: "../fonts/Geist-Variable.woff2", weight: "100 900", style: "normal",
  variable: "--font-geist", display: "swap",
});

export const metadata: Metadata = { title: "Ocean Above", description: "Book flights in plain language" };
export const viewport: Viewport = {
  width: "device-width", initialScale: 1,
  themeColor: [{ media: "(prefers-color-scheme: light)", color: "#faf9f6" }, { media: "(prefers-color-scheme: dark)", color: "#19191b" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={geist.variable} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
