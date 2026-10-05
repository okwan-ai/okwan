import Link from "next/link";
import { OUTCOME_MARK, OUTCOME_TONE } from "@/lib/finding";
import { AGENT_PROMPTS, promptLabel, promptText } from "@/lib/prompts";
import { Badge } from "./ui/badge";
import { CopyButton } from "./ui/copy-button";

/**
 * Six questions an agent can run, each bound to the exact tool call and to
 * the Findings filter that shows the same rows, so the answer can be
 * checked. Static text: no chat and no model here; the customer's own
 * agent runs it, and each run is one metered request.
 */
export function PromptLibrary({ apiBase }: { apiBase: string }) {
  return (
    <div className="space-y-3">
      <ul className="grid gap-3 sm:grid-cols-2">
        {AGENT_PROMPTS.map((p) => (
          <li key={p.id} className="flex min-w-0 flex-col rounded-xl border border-line bg-surface">
            <div className="flex flex-wrap items-start justify-between gap-2 px-4 pt-4">
              <h3 className="text-sm font-semibold">{p.question}</h3>
              {p.outcome ? (
                <Badge tone={OUTCOME_TONE[p.outcome]} symbol={OUTCOME_MARK[p.outcome]}>{promptLabel(p)}</Badge>
              ) : (
                <Badge tone="neutral" symbol="⌕">SQL</Badge>
              )}
            </div>
            <p className="px-4 pt-2 text-sm text-ink-soft">{p.summary}</p>
            <p className="mt-3 border-t border-line bg-canvas/60 px-4 py-2 font-mono text-[12px] leading-relaxed break-all text-ink">{p.call}</p>
            <div className="mt-auto flex flex-wrap items-center gap-1 border-t border-line px-2 py-1.5 text-xs">
              <CopyButton value={promptText(p)} label={`Copy prompt: ${p.question}`} text="Copy prompt" />
              <CopyButton value={p.rest(apiBase)} label={`Copy REST: ${p.question}`} text="REST" />
              {p.outcome ? (
                <Link href={`/findings?outcome=${p.outcome}`} className="ml-auto inline-flex min-h-9 items-center px-2 text-ink-soft underline-offset-4 hover:text-ink hover:underline">
                  Compare with Findings →
                </Link>
              ) : (
                <Link href="/catalog" className="ml-auto inline-flex min-h-9 items-center px-2 text-ink-soft underline-offset-4 hover:text-ink hover:underline">
                  Tables in the catalog →
                </Link>
              )}
            </div>
          </li>
        ))}
      </ul>
      <p className="text-xs text-ink-soft">
        Each run an agent makes from a card is one request against the plan and is stored in that merchant&apos;s run history
        as verdicts, never rail records. An agent sees only what the key&apos;s merchant has connected.
      </p>
    </div>
  );
}
