import { ago, type RunDigest, SURFACE_LABEL } from "@/lib/finding";
import { owedAmount, RunStatus } from "./merchant-status";

/** A merchant's newest stored result, or its readiness when there is none. */
export function LastResult({ ready, known, digest }: { ready: string[]; known: boolean; digest: RunDigest | null }) {
  return (
    <span className="flex flex-wrap items-center gap-2">
      <RunStatus m={{ ready, known }} d={digest} />
      {digest?.ok && digest.twice > 0 && <span className="text-sm font-medium tabular-nums">{owedAmount(digest)} owed back</span>}
      {digest && !digest.ok && digest.detail && <span className="block w-full max-w-[260px] text-xs break-words text-danger">{digest.detail}</span>}
      {digest && (
        <span className="block w-full text-xs text-ink-soft" suppressHydrationWarning>
          Last run {ago(digest.at)}{digest.surface ? ` · ${SURFACE_LABEL[digest.surface] ?? digest.surface}` : ""}
        </span>
      )}
    </span>
  );
}
