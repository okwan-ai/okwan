/* The built refund page says the policy, whole, and nothing else.

   Run after `next build`. It reads the page as it will be served
   (out/refunds.html) and compares every word between <main> and </main>
   with app/refunds/policy.json, the file okwan_api/policy.py writes. A
   word added to the page, left off it, or put there and hidden fails
   the build: what a customer reads is what a bank is sent in a dispute. */
import { readFileSync } from "node:fs";

const policy = JSON.parse(readFileSync(new URL("../app/refunds/policy.json", import.meta.url), "utf8"));
const html = readFileSync(new URL("../out/refunds.html", import.meta.url), "utf8");

const LINK = /\[([^\]]+)\]\(([^)]+)\)/g;
const plain = (line) => line.replace(LINK, "$1");
const squeeze = (text) => text.replace(/\s+/g, " ").trim();
const fail = (why) => {
  console.error(`refund page: ${why}`);
  process.exit(1);
};

const main = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/);
if (!main) fail("no <main> in out/refunds.html");

const entities = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
const shown = squeeze(
  main[1]
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<(script|style)\b[\s\S]*?<\/\1>/g, " ")
    .replace(/<\/?(a|span|strong|em|b|i|u|code|small|sup|sub)\b[^>]*>/gi, "") // words run on through these
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_, dec) => String.fromCodePoint(Number(dec)))
    .replace(/&([a-z]+);/g, (whole, name) => entities[name] ?? whole),
);

const headings = policy.parts.map((part, i) => `${i + 1}. ${part.title}`);
const expected = squeeze(
  [
    `Last updated ${policy.updated}`,
    policy.title,
    plain(policy.intro),
    "Contents",
    ...headings,
    ...policy.parts.flatMap((part, i) => [headings[i], ...part.blocks.flatMap((block) => block.lines.map(plain))]),
  ].join(" "),
);

if (shown !== expected) {
  let at = 0;
  while (at < shown.length && shown[at] === expected[at]) at += 1;
  fail(
    `its words are not the policy's. They part at character ${at}:\n` +
      `  page:   …${shown.slice(Math.max(0, at - 60), at + 80)}\n` +
      `  policy: …${expected.slice(Math.max(0, at - 60), at + 80)}`,
  );
}

/* Every word that is there is shown: nothing in the page is hidden. */
for (const tag of main[1].match(/<[a-z][^>]*>/gi) ?? []) {
  if (/\sstyle=/.test(tag) || /\shidden(\s|=|>)/.test(tag) || /\saria-hidden=/.test(tag)) fail(`an element is styled or hidden: ${tag}`);
  const classes = (tag.match(/\sclass="([^"]*)"/) ?? ["", ""])[1].split(/\s+/);
  const unseen = classes.find((name) => /(^|:)(hidden|invisible|sr-only|opacity-0|text-transparent|text-\[0|w-0|h-0)/.test(name));
  if (unseen) fail(`an element is hidden by "${unseen}": ${tag}`);
}

/* And every link in the policy goes where the policy says. */
for (const [, words, address] of JSON.stringify(policy).matchAll(LINK)) {
  const escaped = address.replace(/&/g, "&amp;");
  if (!main[1].includes(`href="${escaped}"`)) fail(`"${words}" does not link to ${address}`);
}

console.log(`refund page: ${expected.length} characters, version ${policy.version}, exactly the policy.`);
