import { Button } from "@/components/ui/button";
import { AlertIcon, RetryIcon } from "@/components/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** Skeletons shaped like the cards that will replace them (no layout jump when they arrive). */
export function LoadingState({ label = "Loading…", className }: { label?: string; className?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("space-y-3 py-2", className)} data-testid="loading">
      <span className="sr-only">{label}</span>
      <p className="text-sm text-ink-3" aria-hidden>{label}</p>
      <Skeleton className="h-28 max-w-[30rem]" />
      <Skeleton className="h-28 max-w-[30rem]" />
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl bg-danger-bg py-2 pr-2 pl-4 text-sm text-danger" data-testid="error">
      <AlertIcon size={16} className="shrink-0" />
      <span className="min-w-0 flex-1 py-1.5 break-words">{message}</span>
      {onRetry && <Button variant="ghost" className="rounded-full text-danger hover:bg-danger/10" onClick={onRetry}><RetryIcon size={16} />Retry</Button>}
    </div>
  );
}

/** `hero`: the chat's first screen. Default: a quiet dashed panel with an optional icon and action. */
export function EmptyState({ children, hero = false, icon, action }: { children: React.ReactNode; hero?: boolean; icon?: React.ReactNode; action?: React.ReactNode }) {
  if (hero) return <div className="mx-auto max-w-xl pt-[10vh] pb-10 text-center text-sm text-ink-2" data-testid="empty">{children}</div>;
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-border px-6 py-12 text-center text-base text-ink-2" data-testid="empty">
      {icon && <span aria-hidden className="mb-3 inline-flex size-10 items-center justify-center rounded-full bg-sunken text-ink-2">{icon}</span>}
      <div className="max-w-sm">{children}</div>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Page heading block for My tickets / History. */
export function PageHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h1 className="text-xl font-semibold tracking-[-0.02em]">{title}</h1>
      {children && <p className="mt-1 text-sm text-ink-2">{children}</p>}
    </div>
  );
}
