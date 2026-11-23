import Link from "next/link";

/** Small footer with the legal links (login page and legal pages). */
export function SiteFooter({ className = "" }: { className?: string }) {
  return (
    <footer className={`flex flex-wrap items-center justify-center gap-x-2 px-4 py-3 text-sm text-ink-2 ${className}`}>
      <Link href="/privacy" className="inline-flex min-h-11 items-center px-2 underline-offset-4 hover:text-ink hover:underline">Privacy Policy</Link>
      <span aria-hidden>·</span>
      <Link href="/terms" className="inline-flex min-h-11 items-center px-2 underline-offset-4 hover:text-ink hover:underline">Terms of Service</Link>
    </footer>
  );
}
