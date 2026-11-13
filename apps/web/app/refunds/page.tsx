import type { Metadata } from "next";
import { LegalPage, type Part } from "../_components/legal";
import policy from "./policy.json";

/* This page has no words of its own. Every one comes from policy.json,
   which okwan_api/policy.py writes (python -m okwan_api.policy), so the
   page, Stripe Checkout and the copy sent to a bank in a dispute say the
   same thing. tests/test_refund_policy.py fails if the file is not what
   that module writes, or if a word is added here. */

export const metadata: Metadata = {
  title: `${policy.title} — Okwan`,
  description: policy.summary,
  alternates: { canonical: "/refunds" },
};

/* A link is written [words](address) in the file. */
function linked(line: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let at = 0;
  for (const found of line.matchAll(/\[([^\]]+)\]\(([^)]+)\)/g)) {
    out.push(line.slice(at, found.index));
    out.push(
      <a key={found.index} className="font-medium text-ink underline underline-offset-4" href={found[2]}>
        {found[1]}
      </a>,
    );
    at = found.index + found[0].length;
  }
  out.push(line.slice(at));
  return out;
}

const parts: Part[] = policy.parts.map((part) => ({
  id: part.id,
  title: part.title,
  body: part.blocks.map((block, i) =>
    block.kind === "ul" ? (
      <ul key={i}>
        {block.lines.map((line, j) => (
          <li key={j}>{linked(line)}</li>
        ))}
      </ul>
    ) : (
      block.lines.map((line, j) => <p key={`${i}-${j}`}>{linked(line)}</p>)
    ),
  ),
}));

export default function Refunds() {
  return <LegalPage title={policy.title} updated={policy.updated} intro={<p>{linked(policy.intro)}</p>} parts={parts} />;
}
