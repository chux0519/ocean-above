import { Button } from "@/components/ui/button";
import { AlertIcon, RetryIcon } from "@/components/icons";
import { Skeleton } from "@/components/ui/skeleton";

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="space-y-2 py-2" data-testid="loading">
      <span className="sr-only">{label}</span>
      <p className="text-sm text-ink-2" aria-hidden>{label}</p>
      <Skeleton className="h-24 max-w-md" />
      <Skeleton className="h-24 max-w-md" />
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg bg-danger-bg px-4 py-2 text-sm text-danger" data-testid="error">
      <AlertIcon size={16} className="shrink-0" />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onRetry && <Button variant="outline" className="border-danger/40 text-danger" onClick={onRetry}><RetryIcon size={16} />Retry</Button>}
    </div>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return <div className="mx-auto max-w-sm py-10 text-center text-sm text-ink-2" data-testid="empty">{children}</div>;
}
