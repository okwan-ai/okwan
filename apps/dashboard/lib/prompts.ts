/**
 * Prompts an agent can run against the hosted server, one per verdict the
 * `rails` fold produces plus one SQL question. Each is bound to the exact
 * tool call, so the card is both the prompt and its test: an agent's
 * answer can be checked against Findings filtered to the same outcome.
 *
 * Pure: strings only. Tool names and statuses are the hosted server's
 * (okwan_reconcile with name "rails"; OUTCOMES in okwan_recon/across.py).
 * The SQL card's columns are Charge's (okwan_stripe/schemas.py): id,
 * amount, currency, created, order_ref.
 */
import { OUTCOME_LABEL } from "./finding";

export type AgentPrompt = {
  id: string;
  /** The question in plain words. */
  question: string;
  /** What the agent is asked to do with the rows, in one or two sentences. */
  summary: string;
  /** The fold outcome it maps to; absent for the SQL card. */
  outcome?: string;
  /** The prompt, line by line. */
  ask: string[];
  /** The MCP tool call the prompt resolves to. */
  call: string;
  /** The same read over REST. */
  rest: (apiBase: string) => string;
};

const READ_ONLY = "Read only: do not attempt refunds or any write to a payment rail. Okwan cannot make one.";

const EACH_ORDER = "For each order returned, list the order, the rails that took payment, what each took, and the order total.";

function foldAsk(status: string, ...lines: string[]): string[] {
  return [
    `Use the Okwan MCP server. Call okwan_reconcile with name "rails" and status "${status}".`,
    EACH_ORDER,
    ...lines,
    READ_ONLY,
  ];
}

function foldCall(status: string): string {
  return `okwan_reconcile ${JSON.stringify({ name: "rails", status, limit: 200 })}`;
}

function foldRest(status: string): (apiBase: string) => string {
  return (apiBase) => [`curl "${apiBase}/v1/reconciliations/across/rails?outcome=${status}" \\`, `  -H "Authorization: Bearer okw_…"`].join("\n");
}

const SQL = "SELECT id, amount, currency, created FROM stripe.charges WHERE order_ref IS NULL LIMIT 50";

export const AGENT_PROMPTS: AgentPrompt[] = [
  {
    id: "collected_twice",
    outcome: "collected_twice",
    question: "Was any order collected twice?",
    summary: "Total what was taken beyond the order totals: that is owed back to customers.",
    ask: foldAsk("collected_twice", "Total what was taken beyond the order totals: that is owed back to customers."),
    call: foldCall("collected_twice"),
    rest: foldRest("collected_twice"),
  },
  {
    id: "uncollected",
    outcome: "uncollected",
    question: "Which orders have no payment?",
    summary: "Group them by day. Say which look like unpaid orders and which could be a rail Okwan was not given.",
    ask: foldAsk("uncollected", "Group them by day. Say which look like unpaid orders and which could be a rail Okwan was not given."),
    call: foldCall("uncollected"),
    rest: foldRest("uncollected"),
  },
  {
    id: "collected_inconsistent",
    outcome: "collected_inconsistent",
    question: "What doesn't add up across rails?",
    summary: "For each, state the difference between what was taken and the order total, and which rail you would check first.",
    ask: foldAsk("collected_inconsistent", "For each, state the difference between what was taken and the order total, and which rail you would check first."),
    call: foldCall("collected_inconsistent"),
    rest: foldRest("collected_inconsistent"),
  },
  {
    id: "unverifiable",
    outcome: "unverifiable",
    question: "What couldn't be verified, and why?",
    summary: "Use each row's reason. Say what a person would need (a credential, a date range, a currency) to settle it.",
    ask: foldAsk("unverifiable", "Use each row's reason. Say what a person would need (a credential, a date range, a currency) to settle it."),
    call: foldCall("unverifiable"),
    rest: foldRest("unverifiable"),
  },
  {
    id: "split_tender",
    outcome: "split_tender",
    question: "Which orders were split across rails?",
    summary: "These are paid in full; report them as information, not as problems.",
    ask: foldAsk("split_tender", "These are paid in full; report them as information, not as problems."),
    call: foldCall("split_tender"),
    rest: foldRest("split_tender"),
  },
  {
    id: "stripe_no_ref",
    question: "Which Stripe charges carry no order reference?",
    summary: "A charge with no order reference can't be matched to an order by the rails fold. List them, and say what the merchant could set in Stripe metadata to fix it.",
    ask: [
      "Use the Okwan MCP server. Call okwan_describe_tables, then okwan_query with this SQL:",
      SQL,
      "A charge with no order reference can't be matched to an order by the rails fold. List them with amounts in minor units, and say what the merchant could set in Stripe metadata to fix it.",
      READ_ONLY,
    ],
    call: `okwan_query ${JSON.stringify({ sql: SQL })}`,
    rest: (apiBase) =>
      [
        `curl -X POST "${apiBase}/v1/query" \\`,
        `  -H "Authorization: Bearer okw_…" -H "Content-Type: application/json" \\`,
        `  -d '${JSON.stringify({ sql: SQL })}'`,
      ].join("\n"),
  },
];

/** What "Copy prompt" copies: the prompt, then the call it should make. */
export function promptText(p: AgentPrompt): string {
  return [...p.ask, "", `Tool call: ${p.call}`].join("\n");
}

export function promptLabel(p: AgentPrompt): string {
  return p.outcome ? OUTCOME_LABEL[p.outcome] ?? p.outcome : "SQL";
}
