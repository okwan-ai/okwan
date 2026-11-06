import { Fragment, type ReactNode } from "react";

/** The assistant writes light Markdown. Rendered from text, never as HTML:
 *  paragraphs, "- " lists, **bold**, `code` and links. */
/** `numbered`: ordered steps drawn as purple numbered circles (Support). */
export function Rich({ text, numbered = false }: { text: string; numbered?: boolean }) {
  // Line by line, so a list right under a heading ("**How to fix it:**"
  // then "- ...") is still a list: runs of list lines become one list, and
  // runs of other lines one paragraph.
  type Group = { kind: "p" | "ul" | "ol"; rows: string[] };
  const groups: Group[] = [];
  for (const line of text.split("\n")) {
    const kind = /^\s*[-*] /.test(line) ? "ul" : /^\s*\d+\. /.test(line) ? "ol" : "p";
    const last = groups[groups.length - 1];
    if (!line.trim()) {
      if (last && last.kind === "p") groups.push({ kind: "p", rows: [] });
      continue;
    }
    if (last && last.kind === kind) {
      last.rows.push(line);
    } else {
      groups.push({ kind, rows: [line] });
    }
  }
  const shown = groups.filter((g) => g.rows.length);
  return (
    <div className="space-y-2.5">
      {shown.map((g, i) => {
        if (g.kind === "ul") {
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {g.rows.map((r, j) => <li key={j}><Inline text={r.replace(/^\s*[-*] /, "")} /></li>)}
            </ul>
          );
        }
        if (g.kind === "ol") {
          return (
            <ol key={i} className={numbered ? "space-y-3" : "list-decimal space-y-1 pl-5"}>
              {g.rows.map((r, j) => (
                <li key={j} className={numbered ? "flex gap-3" : undefined}>
                  {numbered && (
                    <span aria-hidden className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[linear-gradient(135deg,var(--color-accent),var(--color-accent-2))] text-[13px] font-bold text-white shadow-lift">
                      {j + 1}
                    </span>
                  )}
                  <span className={numbered ? "min-w-0" : undefined}><Inline text={r.replace(/^\s*\d+\. /, "")} /></span>
                </li>
              ))}
            </ol>
          );
        }
        return <p key={i} className="whitespace-pre-wrap"><Inline text={g.rows.join("\n")} /></p>;
      })}
    </div>
  );
}

function Inline({ text }: { text: string }) {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((?:https?:\/\/|\/)[^)\s]+\)|https?:\/\/[^\s)]+)/g;
  let at = 0;
  for (const m of text.matchAll(pattern)) {
    const token = m[0];
    parts.push(text.slice(at, m.index));
    if (token.startsWith("**")) parts.push(<strong key={m.index}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith("`")) {
      parts.push(<code key={m.index} className="rounded bg-canvas px-1 py-0.5 font-mono text-[12.5px]">{token.slice(1, -1)}</code>);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      const href = link ? link[2] : token;
      parts.push(
        <a key={m.index} href={href} className="font-medium underline decoration-ink/30 underline-offset-2 hover:decoration-ink"
          {...(href.startsWith("http") ? { target: "_blank", rel: "noreferrer" } : {})}>
          {link ? link[1] : token}
        </a>,
      );
    }
    at = (m.index ?? 0) + token.length;
  }
  parts.push(text.slice(at));
  return <>{parts.map((p, i) => <Fragment key={i}>{p}</Fragment>)}</>;
}
