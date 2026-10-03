"use client";

import { useState } from "react";
import { IconCheck, IconCopy } from "./icons";

/** Copies `value`. Icon-only by default, so it carries an aria-label. */
export function CopyButton({ value, label = "Copy", showText = false }: { value: string; label?: string; showText?: boolean }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={showText ? undefined : copied ? "Copied" : label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
      className="inline-flex min-h-11 min-w-11 items-center justify-center gap-1.5 rounded-lg px-2 text-sm text-ink-soft hover:bg-ink/5 hover:text-ink"
    >
      {copied ? <IconCheck /> : <IconCopy />}
      {showText && <span>{copied ? "Copied" : label}</span>}
      <span role="status" className="sr-only">{copied ? "Copied" : ""}</span>
    </button>
  );
}

/** A code block with a copy control, for snippets meant to be pasted. */
export function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="relative rounded-xl border border-line bg-canvas">
      <div className="flex items-center justify-between border-b border-line py-1 pr-1 pl-4">
        <span className="text-xs font-medium text-ink-soft">{label}</span>
        <CopyButton value={code} label={`Copy ${label}`} showText />
      </div>
      <pre className="overflow-x-auto px-4 py-3 font-mono text-xs leading-relaxed text-ink"><code>{code}</code></pre>
    </div>
  );
}
