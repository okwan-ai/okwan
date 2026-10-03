export function Skeleton({ className = "" }: { className?: string }) {
  return <span aria-hidden className={`block animate-pulse rounded-md bg-ink/[0.07] ${className}`} />;
}

/** Placeholder rows shaped like the table they stand in for. */
export function SkeletonRows({ rows = 5, cols = 4, label = "Loading" }: { rows?: number; cols?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="overflow-hidden rounded-xl border border-line bg-surface">
      <div className="border-b border-line bg-canvas/60 px-4 py-3"><Skeleton className="h-3 w-40" /></div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex items-center gap-6 border-b border-line px-4 py-3.5 last:border-b-0">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={c} className={`h-3.5 ${c === 0 ? "w-1/3" : "w-1/6"}`} />
          ))}
        </div>
      ))}
    </div>
  );
}
