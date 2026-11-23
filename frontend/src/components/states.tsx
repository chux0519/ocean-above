import { Button } from "@/components/ui/button";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2 py-2" data-testid="loading">
      <span className="sr-only">{label}</span>
      <p className="text-sm text-muted" aria-hidden>{label}</p>
      <div className="h-16 animate-pulse rounded-lg bg-slate-100" aria-hidden />
      <div className="h-16 animate-pulse rounded-lg bg-slate-100" aria-hidden />
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-danger" data-testid="error">
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onRetry && <Button variant="outline" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <p className="py-6 text-center text-sm text-muted" data-testid="empty">{children}</p>;
}
