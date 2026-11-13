/* The refund page, as a browser shows it, says the policy, whole, and
   nothing else.

   Run after `next build`. It serves the built site (out/), opens
   /refunds in a real browser, and compares what a reader is shown with
   app/refunds/policy.json, the file okwan_api/policy.py writes:

   - the words on the screen between <main> and </main> are exactly the
     policy's: a word added, left off, or in the HTML but not rendered
     fails;
   - nothing is written by a stylesheet (::before, ::after), and no text
     is there but unreadable (transparent, the colour of what is behind
     it, too small, off the page);
   - around it the page has only its frame, the site's own navigation: a
     sentence above or below the policy fails too;
   - every link in the policy goes where the policy says.

   What a customer reads is what a bank is sent in a dispute. This guards
   against drift and mistakes. It is not proof against an author set on
   hiding something: there are more ways to style text than a check can
   measure, and a change to this page is read by a person like any other. */
import { existsSync, readFileSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const { chromium } = createRequire(import.meta.url)("playwright");
const out = fileURLToPath(new URL("../out/", import.meta.url));
const policy = JSON.parse(readFileSync(new URL("../app/refunds/policy.json", import.meta.url), "utf8"));

/* The page's frame: the words around <main> on every legal page
   (app/_components/legal.tsx). If the navigation changes, change it here. */
const ABOVE = "Privacy Terms Refunds Security";
const BELOW = "© 2026 Global Tech Startup LLC Home Privacy Terms Refunds Security Contact";

const LINK = /\[([^\]]+)\]\(([^)]+)\)/g;
const plain = (line) => line.replace(LINK, "$1");
const squeeze = (text) => text.replace(/\s+/g, " ").trim();
const fail = (why) => {
  console.error(`refund page: ${why}`);
  process.exit(1);
};
const parted = (shown, expected, what) => {
  let at = 0;
  while (at < shown.length && shown[at] === expected[at]) at += 1;
  fail(
    `${what}. They part at character ${at}:\n` +
      `  shown:    …${shown.slice(Math.max(0, at - 60), at + 80)}\n` +
      `  expected: …${expected.slice(Math.max(0, at - 60), at + 80)}`,
  );
};

if (!existsSync(join(out, "refunds.html"))) fail("out/refunds.html is not there: run `next build` first");

const TYPES = { ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".json": "application/json", ".woff2": "font/woff2", ".txt": "text/plain" };
const server = createServer((request, response) => {
  const path = normalize(decodeURIComponent(new URL(request.url, "http://x").pathname));
  const file = [join(out, path), join(out, `${path}.html`), join(out, path, "index.html")].find(
    (candidate) => candidate.startsWith(out) && existsSync(candidate) && statSync(candidate).isFile(),
  );
  if (!file) return response.writeHead(404).end();
  response.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" }).end(readFileSync(file));
});
await new Promise((ready) => server.listen(0, "127.0.0.1", ready));

const headings = policy.parts.map((part, i) => `${i + 1}. ${part.title}`);
const expected = squeeze(
  [
    `Last updated ${policy.updated}`.toUpperCase(), // the page sets this line in capitals
    policy.title,
    plain(policy.intro),
    "Contents",
    ...headings,
    ...policy.parts.flatMap((part, i) => [headings[i], ...part.blocks.flatMap((block) => block.lines.map(plain))]),
  ].join(" "),
);

const browser = await chromium.launch();
let seen;
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const answer = await page.goto(`http://127.0.0.1:${server.address().port}/refunds`, { waitUntil: "networkidle" });
  if (!answer || !answer.ok()) fail(`the built page did not load (${answer?.status()})`);
  seen = await page.evaluate(() => {
    const main = document.querySelector("main");
    const colour = (value) => (value.match(/[\d.]+/g) ?? []).map(Number);
    const behind = (element) => {
      for (let at = element; at; at = at.parentElement) {
        const [r, g, b, a = 1] = colour(getComputedStyle(at).backgroundColor);
        if (a > 0.5) return [r, g, b];
      }
      return [255, 255, 255];
    };
    const written = [];
    const unreadable = [];
    for (const element of document.body.querySelectorAll("*")) {
      for (const pseudo of ["::before", "::after"]) {
        const content = getComputedStyle(element, pseudo).content;
        if (/[\p{L}\p{N}]/u.test(content.replace(/^(none|normal)$/, ""))) written.push(`${element.tagName.toLowerCase()}${pseudo} ${content}`);
      }
      if (!main.contains(element)) continue;
      const own = [...element.childNodes].filter((node) => node.nodeType === 3 && node.textContent.trim()).map((node) => node.textContent.trim()).join(" ");
      if (!own || !element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue; // not rendered: not in innerText either
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      const [r, g, b, a = 1] = colour(style.color);
      const [br, bg, bb] = behind(element);
      const why =
        parseFloat(style.fontSize) < 9 ? "smaller than a reader can read"
        : a < 0.5 ? "transparent"
        : Math.abs(r - br) + Math.abs(g - bg) + Math.abs(b - bb) < 60 ? "the colour of what is behind it"
        : box.width < 1 || box.height < 1 ? "given no room"
        : box.right < 1 || box.left > document.documentElement.scrollWidth - 1 || box.bottom + scrollY < 1 ? "off the page"
        : style.clipPath !== "none" || (style.clip !== "auto" && style.position === "absolute") ? "clipped away"
        : null;
      if (why) unreadable.push(`"${own.slice(0, 60)}" is ${why}`);
    }
    return {
      main: main.innerText,
      body: document.body.innerText,
      written,
      unreadable,
      links: [...main.querySelectorAll("a[href]")].map((a) => [a.innerText.trim(), a.getAttribute("href")]),
    };
  });
} finally {
  await browser.close();
  server.close();
}

const shown = squeeze(seen.main);
if (shown !== expected) parted(shown, expected, "the words on the screen are not the policy's");
if (seen.written.length) fail(`a stylesheet writes words onto the page: ${seen.written.join("; ")}`);
if (seen.unreadable.length) fail(`text is on the page but not readable: ${seen.unreadable.join("; ")}`);
const whole = squeeze(seen.body);
const framed = `${ABOVE} ${expected} ${BELOW}`;
if (whole !== framed) parted(whole, framed, "the page shows more than its frame and the policy");
const lines = [policy.intro, ...policy.parts.flatMap((part) => part.blocks.flatMap((block) => block.lines))];
const links = lines.flatMap((line) => [...line.matchAll(LINK)].map(([, words, address]) => [words, address]));
for (const [words, address] of links) {
  if (!seen.links.some(([text, href]) => text === words && href === address)) fail(`"${words}" does not link to ${address}`);
}
if (seen.links.filter(([, href]) => !href.startsWith("#")).length !== links.length) fail("the page links somewhere the policy does not");

console.log(`refund page: ${expected.length} characters shown, version ${policy.version}, exactly the policy.`);
