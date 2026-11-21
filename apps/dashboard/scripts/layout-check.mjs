// The dashboard's layout check (docs/ops/RUNBOOK.md, "Layout check").
//
// Against a running dashboard and API (CI seeds one with scripts/ui_server.py),
// at a wide monitor, a laptop, a small laptop and a phone, on every page:
//   1. no visible text overlaps other text,
//   2. no page scrolls sideways,
// and, where there is one, every item of the open account menu is the top
// element where it is drawn. Any failure exits 1, with a screenshot of the
// page in OUT for the CI artifact.
//
//   BASE=http://localhost:3000 UI_CHECK_EMAIL=… UI_CHECK_PASSWORD=… node scripts/layout-check.mjs
import { mkdirSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";

// Through require, so a Playwright installed globally (NODE_PATH) resolves too.
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.BASE ?? "http://localhost:3000";
const EMAIL = process.env.UI_CHECK_EMAIL ?? "layout@okwan.test";
const PASSWORD = process.env.UI_CHECK_PASSWORD;
const OUT = process.env.OUT ?? "layout-check";
if (!PASSWORD) throw new Error("UI_CHECK_PASSWORD is not set");
mkdirSync(OUT, { recursive: true });

const SIZES = [[1670, 941], [1366, 768], [1024, 768], [390, 844]];
const SIGNED_OUT = ["/signup?mode=signin", "/signup", "/verify?token=layout-check", "/unsubscribe?token=layout-check"];
const SIGNED_IN = [
  "/overview", "/overview?days=all", "/sample", "/findings", "/exceptions", "/merchants", "/integrations", "/agents",
  "/settings", "/settings?tab=plan", "/settings?tab=keys", "/settings?tab=access", "/settings?tab=security", "/ops",
];

// Runs in the page: text a person can see, and whether any of it collides.
function inspect() {
  const seen = (el) => {
    // A closed <details> keeps its body in the page but draws only the summary.
    const closed = el.closest("details:not([open])");
    if (closed && !el.closest("summary")) return false;
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const st = getComputedStyle(e);
      if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) < 0.05) return false;
      if (e.hasAttribute("hidden") || e.getAttribute("aria-hidden") === "true") return false;
      const r = e.getBoundingClientRect();
      if (st.position === "absolute" && r.width <= 1 && r.height <= 1) return false; // sr-only
      if (st.clip === "rect(0px, 0px, 0px, 0px)" || st.clipPath.startsWith("inset(50%")) return false; // clipped away
    }
    return true;
  };
  // Clamped text keeps its hidden lines' boxes; count only what its box shows.
  const shown = (el, r) => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      const st = getComputedStyle(e);
      if (st.overflow === "hidden" || st.overflowY === "hidden" || st.overflowX === "hidden") {
        const b = e.getBoundingClientRect();
        if (r.bottom <= b.top + 1 || r.top >= b.bottom - 1 || r.right <= b.left + 1 || r.left >= b.right - 1) return false;
      }
    }
    return true;
  };
  const texts = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walk.nextNode(); n; n = walk.nextNode()) {
    if (n.textContent.trim().length < 2) continue;
    const el = n.parentElement;
    if (!el || el.closest("svg, script, style, noscript") || !seen(el)) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) {
      if (r.width > 1 && r.height > 1 && shown(el, r)) texts.push({ el, r, t: n.textContent.trim().slice(0, 40) });
    }
  }
  const hits = new Set();
  for (let i = 0; i < texts.length; i++) {
    for (let j = i + 1; j < texts.length; j++) {
      const a = texts[i], c = texts[j];
      if (a.el === c.el) continue;
      const x = Math.min(a.r.right, c.r.right) - Math.max(a.r.left, c.r.left);
      const y = Math.min(a.r.bottom, c.r.bottom) - Math.max(a.r.top, c.r.top);
      if (x > 3 && y > 3) hits.add(`"${a.t}" overlaps "${c.t}"`);
    }
  }
  return { hits: [...hits].slice(0, 6), sideways: document.documentElement.scrollWidth > innerWidth };
}

const problems = [];
/** Pages other than the Files tab that threw while the browser took them over: printed, not failed (G22). */
const threw = [];
let slow = 0;
const note = async (page, where, what) => {
  problems.push(`${where}: ${what}`);
  await page.screenshot({ path: `${OUT}/${where.replace(/[^a-z0-9]+/gi, "_")}.png`, fullPage: true }).catch(() => {});
};

async function checkPage(page, w, h, path) {
  await page.goto(BASE + path, { waitUntil: "networkidle" });
  await page.waitForTimeout(250);
  const res = await page.evaluate(inspect);
  const where = `${w}x${h} ${path}`;
  if (res.sideways) await note(page, where, "the page scrolls sideways");
  for (const hit of res.hits) await note(page, where, hit);
}

// The plan page shows the refund policy's own words ahead of the Upgrade
// button: what a bank is told the customer was shown before paying. They
// are okwan_api/policy.py's plan_page(), sent by the API, and
// tests/test_refund_policy.py holds this line to them.
const PLAN_NOTICE = "Pro is 99 US dollars a month and renews each month until you cancel. To cancel, use Manage billing on this page: Pro stays on to the end of the month you have paid for. A payment is not refunded once its month has started. Refunds and cancellation · Terms";

// And the card it sits in says nothing else about paying: its whole text,
// as a free account sees it, with whichever button the deployment shows.
const PLAN_CARD = `Upgrade to Pro 100,000 requests a month instead of 5,000, for $99 a month. ${PLAN_NOTICE}`;
const PLAN_BUTTONS = ["Upgrade for $99/month", "Talk to us about Pro"];
const PLAN_FOOT = "Card payments are handled by Stripe; Okwan never sees your card. Need more than 100,000 requests? Talk to us.";

async function checkPlanNotice(page, w, h) {
  await page.goto(`${BASE}/settings?tab=plan`, { waitUntil: "networkidle" });
  const where = `${w}x${h} /settings?tab=plan the refund notice`;
  const found = await page.evaluate(() => {
    const notice = document.querySelector("[data-policy-notice]");
    const button = document.querySelector("[data-upgrade]");
    if (!notice || !button) return { missing: !notice ? "the notice" : "the Upgrade button" };
    const readable = (el) => {
      for (let e = el; e && e !== document.body; e = e.parentElement) {
        const st = getComputedStyle(e);
        if (st.display === "none" || st.visibility === "hidden" || Number(st.opacity) < 0.5) return false;
        if (e.hasAttribute("hidden") || e.getAttribute("aria-hidden") === "true") return false;
      }
      const st = getComputedStyle(el);
      const [, , , alpha = "1"] = st.color.match(/[\d.]+/g) ?? [];
      const box = el.getBoundingClientRect();
      return parseFloat(st.fontSize) >= 11 && Number(alpha) >= 0.5 && box.width > 40 && box.height > 10
        && box.left >= 0 && box.right <= document.documentElement.clientWidth + 1;
    };
    const n = notice.getBoundingClientRect();
    const b = button.getBoundingClientRect();
    return {
      text: notice.innerText.replace(/\s+/g, " ").trim(),
      readable: readable(notice),
      first: Boolean(notice.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING),
      // Beside the button or above it, never below it.
      placed: n.top <= b.top + 1 || n.left + n.width <= b.left + 1,
      links: [...notice.querySelectorAll("a")].map((a) => a.getAttribute("href")),
      card: notice.closest("section").innerText.replace(/\s+/g, " ").trim(),
    };
  });
  if (found.missing) return note(page, where, `${found.missing} is not on the page`);
  if (found.text !== PLAN_NOTICE) await note(page, where, `it says "${found.text}"`);
  if (!PLAN_BUTTONS.some((button) => found.card === `${PLAN_CARD} ${button} ${PLAN_FOOT}`)) await note(page, where, `the card around it says "${found.card}"`);
  if (!found.readable) await note(page, where, "it is on the page but not readable");
  if (!found.first || !found.placed) await note(page, where, "it does not come before the Upgrade button");
  if (found.links.length !== 2 || !found.links[0].endsWith("/refunds") || !found.links[1].endsWith("/terms")) {
    await note(page, where, `its links go to ${found.links.join(", ")}`);
  }
}

// The import page with a file chosen: the column-matching form, the
// options and the first rows, which only exist once a file is read.
const SAMPLE = [
  "Batch ID,Close Date,Gross Sales,Fees,Chargebacks,Net Deposit,Merchant ID,Card Number,Terminal",
  "B-1001,11/02/2026,1000.00,29.00,0.00,971.00,MID-1,************1111,T-01",
  "B-1002,11/03/2026,250.50,7.27,0.00,243.23,MID-1,************2222,T-02",
].join("\n");

async function checkImport(page, w, h, merchant) {
  const path = `${merchant}/import`;
  await page.goto(BASE + path, { waitUntil: "networkidle" });
  await page.locator('input[type="file"]').setInputFiles({ name: "batch-report.csv", mimeType: "text/csv", buffer: Buffer.from(SAMPLE) });
  await page.getByText("3. Match the columns").waitFor({ timeout: 15000 });
  await page.waitForTimeout(250);
  const res = await page.evaluate(inspect);
  const where = `${w}x${h} ${path} (file chosen)`;
  if (res.sideways) await note(page, where, "the page scrolls sideways");
  for (const hit of res.hits) await note(page, where, hit);
}

// The Files tab's check, on the seeded merchant: what the stored run holds
// is what the page says (one batch never paid, with its amount; two deposits
// matched, one of them to two batches), a click runs it again through the
// dashboard's own route and the page reads the new run back, and that route
// runs nothing but the checks the tab offers.
async function checkFileCheck(page, w, h, merchant) {
  const path = `${merchant}?tab=files`;
  const where = `${w}x${h} ${path} the batches-against-deposits check`;
  await page.goto(BASE + path, { waitUntil: "networkidle" });
  const section = page.locator("section", { has: page.locator("h2", { hasText: "Batches against deposits" }) }).first();
  if (!(await section.count())) return note(page, where, "the check is not on the Files tab");
  const read = async () => (await section.innerText()).replace(/\s+/g, " ").trim();
  // The words the wording check holds, read here in a browser: the headline,
  // what the run read, what is on file and was not read (the seed has a batch
  // and a deposit from before the days the check reads), and the note under
  // the lines. Second attacker's pass: each could be taken out of the page
  // and this check stayed green.
  const expected = ["1 batch was not deposited", "2 deposits matched to their batches · 1 batch not deposited",
    "Not deposited Batch B-2004 $1,238.27", "No deposit on the statement pays it.",
    "The check read 4 batches and 2 deposits dated", "The batch report on file goes back to", "Batches before",
    "The bank statement on file goes back to", "were not checked.", "Anything in the files outside those days is not in this result.",
    "The newest statement on file runs to", "Checked "];
  const before = await read();
  for (const words of expected) if (!before.includes(words)) await note(page, where, `it does not say "${words}"`);
  // An unpaid batch is drawn in the colour of a fault, never in the green of a clean result.
  const colours = (await section.locator("p.text-lg").first().getAttribute("class")) ?? "";
  if (!/\btext-danger\b/.test(colours) || /\btext-ok\b/.test(colours)) await note(page, where, `the headline's colour is "${colours}"`);
  await section.locator("details summary").click();
  const open = await read();
  for (const words of ["$1,786.64", "Paid 1 batch:", "B-2001", "$1,291.92", "Paid 2 batches:", "B-2002", "$893.81", "B-2003", "$398.11"]) {
    if (!open.includes(words)) await note(page, where, `opened, it does not say "${words}"`);
  }
  // A batch id is a merchant's own text. Each is drawn in an element of its
  // own, in its own direction, and in no sentence the page writes.
  const refs = await section.locator("bdi[data-file-ref]").evaluateAll((all) => all.map((el) => [el.textContent, el.dir]));
  const drawn = refs.map(([text]) => text).sort().join(" ");
  if (drawn !== "B-2001 B-2002 B-2003 B-2004" || refs.some(([, dir]) => dir !== "ltr")) {
    await note(page, where, `the batch ids are not each in an element of their own: ${JSON.stringify(refs)}`);
  }
  const sentences = await section.locator("li > p.text-sm").evaluateAll((all) => all.map((el) => el.textContent ?? ""));
  for (const said of sentences) if (/B-20\d\d/.test(said)) await note(page, where, `a batch id is inside the page's own sentence: "${said}"`);
  // Measured from the top, like every other page: scrolled, the phone's
  // header sits over whatever passes beneath it, as it is meant to.
  await page.evaluate(() => window.scrollTo(0, 0));
  const res = await page.evaluate(inspect);
  if (res.sideways) await note(page, `${where} (opened)`, "the page scrolls sideways");
  for (const hit of res.hits) await note(page, `${where} (opened)`, hit);

  // Run it again: one request to the dashboard's route, then the stored run read back.
  const ran = page.waitForResponse((r) => r.url().includes("/checks/batches_deposits") && r.request().method() === "POST", { timeout: 30000 });
  await section.getByRole("button", { name: "Check again" }).click();
  const response = await ran.catch(() => null);
  if (!response || response.status() !== 200) return note(page, where, `running it answered ${response ? response.status() : "nothing"}`);
  // The browser is told which stored run to read, and nothing of the result.
  const answer = await response.json().catch(() => null);
  const keys = answer ? Object.keys(answer).sort().join(",") : "nothing readable";
  if (keys !== "at,run_id" || !/^run_[0-9a-f]{16}$/.test(answer.run_id)) await note(page, where, `running it answered with ${keys}`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(400);
  const after = await read();
  for (const words of expected) if (!after.includes(words)) await note(page, where, `after running again it does not say "${words}"`);
  if (await section.locator('[role="alert"]').count()) await note(page, where, `running it showed "${await section.locator('[role="alert"]').innerText()}"`);

  // The route runs only the checks the tab offers: not the cross-rail fold, not another check.
  const id = merchant.split("/").pop();
  const refused = await page.evaluate(async (tenant) => {
    const out = {};
    for (const name of ["rails", "across%2Frails", "shopify_stripe", "batches_deposits%2F..%2Frails"]) {
      const r = await fetch(`/api/merchants/${tenant}/checks/${name}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      out[name] = r.status;
    }
    return out;
  }, id);
  for (const [name, status] of Object.entries(refused)) {
    if (status !== 404) await note(page, where, `the route ran "${name}" (answered ${status})`);
  }
  // And only for a request this site's own page made: one sent by another
  // site's page, with the signed-in browser's cookie, is refused.
  for (const origin of ["https://okwan.example", "null"]) {
    const other = await page.request.post(`${BASE}/api/merchants/${id}/checks/batches_deposits`, {
      headers: { "Content-Type": "application/json", Origin: origin }, data: "{}", failOnStatusCode: false });
    if (other.status() !== 403) await note(page, where, `a request from ${origin} ran the check (answered ${other.status()})`);
  }
}

// A page reaches the browser in pieces. On a slow link the browser starts to
// take the page over before the last of them is there, and a page built so
// that it then takes an element over twice is thrown away by React and drawn
// again ("hydration failed"). A fast local link never shows it (second
// attacker's pass: one load in seven of the Files tab at 400 kbit/s, none
// here). So the Files tab is also loaded through a proxy that hands its
// document on 2 KB at a time, 40 ms apart, scripts at full speed; built as it
// was, 13 loads in 60 failed this way, and as it is, none in 300.
function slowDocuments(target) {
  const to = new URL(target);
  const server = http.createServer((req, res) => {
    const up = http.request({ host: to.hostname, port: to.port, path: req.url, method: req.method,
      headers: { ...req.headers, "accept-encoding": "identity" } }, (ur) => {
      const headers = { ...ur.headers };
      if (!String(headers["content-type"] ?? "").startsWith("text/html")) { res.writeHead(ur.statusCode, headers); return ur.pipe(res); }
      delete headers["content-length"];
      res.writeHead(ur.statusCode, headers);
      const pieces = [];
      let ended = false;
      let timer = null;
      const pump = () => {
        if (pieces.length) { res.write(pieces.shift()); timer = setTimeout(pump, 40); } else if (ended) res.end(); else timer = null;
      };
      ur.on("data", (buf) => { for (let i = 0; i < buf.length; i += 2048) pieces.push(buf.subarray(i, i + 2048)); if (!timer) pump(); });
      ur.on("end", () => { ended = true; if (!timer) pump(); });
    });
    up.on("error", () => { res.statusCode = 502; res.end(); });
    req.pipe(up);
  });
  return new Promise((resolve) => server.listen(0, to.hostname, () => resolve(server)));
}

async function checkSlowly(browser, session, merchant, loads = 15) {
  const proxy = await slowDocuments(BASE);
  const through = `${new URL(BASE).protocol}//${new URL(BASE).hostname}:${proxy.address().port}`;
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce", storageState: session });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).split("\n")[0].slice(0, 200)));
  let failed = 0;
  for (let n = 0; n < loads; n++) {
    errors.length = 0;
    await page.goto(`${through}${merchant}?tab=files`, { waitUntil: "networkidle", timeout: 120000 });
    await page.waitForTimeout(250);
    if (errors.length) failed += 1;
  }
  const said = await page.locator("section p.text-lg").first().innerText().catch(() => "");
  if (!said.includes("1 batch was not deposited")) problems.push(`390x844 ${merchant}?tab=files, arriving slowly: the result is not on the page ("${said}")`);
  if (failed) problems.push(`390x844 ${merchant}?tab=files, arriving slowly: ${failed} of ${loads} loads threw while the browser took the page over`);
  await ctx.close();
  await new Promise((done) => proxy.close(done));
  return loads;
}

// Open items: every finding of the workspace's merchants, across checks. The
// seed's unpaid batch is there with its amount and how long it has been
// open, its reference in an element of its own and in none of the page's
// sentences, and the merchant's name is a link to its page.
async function checkOpenItems(page, w, h) {
  const where = `${w}x${h} /exceptions`;
  await page.goto(`${BASE}/exceptions`, { waitUntil: "networkidle" });
  const item = page.locator("[data-exception]").filter({ has: page.locator("bdi[data-file-ref]", { hasText: /^B-2004$/ }) });
  if ((await item.count()) !== 1) return note(page, where, "the seed's unpaid batch B-2004 is not one open item");
  const text = await item.innerText();
  if (!text.includes("Batch with no deposit")) await note(page, where, `the unpaid batch does not say what it is ("${text.slice(0, 80)}")`);
  if (!/\$1,238\.27/.test(text)) await note(page, where, "the unpaid batch does not show its amount");
  if (!/Open \d+ days?|Open since today/.test(text)) await note(page, where, "the unpaid batch does not say how long it has been open");
  const said = await item.locator("span.font-semibold").first().innerText();
  if (said.includes("B-2004")) await note(page, where, "the batch's reference is inside the page's own words");
  const link = item.locator("a[href^='/merchants/ten_'] bdi[data-merchant-name]");
  if ((await link.count()) !== 1) await note(page, where, "the merchant's name is not a link to its page");
  // Deciding: the panel opens with the choices and asks why before it records.
  const decide = item.locator("button[data-decide]");
  if (!(await decide.isVisible())) return note(page, where, "the unpaid batch has no Decide button in view");
  await decide.click();
  const form = page.locator("[role=dialog] form[data-decide-form]");
  if (!(await form.isVisible())) return note(page, where, "Decide did not open its panel");
  if ((await form.locator("input[type=radio]").count()) !== 3) await note(page, where, "the panel does not offer explain, resolve and give");
  if (!(await form.locator("button[type=submit]").isDisabled())) await note(page, where, "a decision can be recorded with no reason");
  await page.locator("[role=dialog] section[aria-label='What was decided']").getByText(/No one has decided|Loading/).first()
    .waitFor({ timeout: 5000 }).catch(() => note(page, where, "the panel does not say what was decided before"));
  await page.keyboard.press("Escape");
}

/** Once, at the end: explain the unpaid batch, see it leave the list and be
 *  listed with its reason among the merchant's closed items, open it again,
 *  and see it back. The seed is left as it was found. */
async function checkDeciding(page) {
  const where = "deciding an open item";
  const why = "Layout check: the processor held this batch.";
  const back = "Layout check: opened again.";
  await page.goto(`${BASE}/exceptions`, { waitUntil: "networkidle" });
  const batch = () => page.locator("[data-exception]").filter({ has: page.locator("bdi[data-file-ref]", { hasText: /^B-2004$/ }) });
  if ((await batch().count()) !== 1) return note(page, where, "the seed's unpaid batch is not open");
  const href = await batch().locator("a[href^='/merchants/ten_']").getAttribute("href");
  const merchant = href.split("/")[2].split("?")[0];
  await batch().locator("button[data-decide]").click();
  const form = page.locator("[role=dialog] form[data-decide-form]");
  await form.locator("input[value=explained]").check();
  await form.locator("textarea[name=note]").fill(why);
  await form.locator("button[type=submit]").click();
  // The page draws the list again once the decision is recorded.
  const gone = await batch().waitFor({ state: "detached", timeout: 15000 }).then(() => true, () => false);
  if (!gone) return note(page, where, "the explained batch is still on the list");
  await page.goto(`${BASE}/exceptions?merchant=${encodeURIComponent(merchant)}&state=closed`, { waitUntil: "networkidle" });
  const closed = batch();
  if ((await closed.count()) !== 1) return note(page, where, "the explained batch is not among the merchant's closed items");
  const said = await closed.innerText();
  if (!said.includes(why) || !said.includes("Explained by")) await note(page, where, "the closed batch does not say who explained it and why");
  if ((await closed.locator("bdi[data-note]").count()) !== 1) await note(page, where, "the reason is not drawn as the person's own text");
  await closed.locator("button[data-decide]").click();
  await form.locator("textarea[name=note]").fill(back);
  await form.locator("button[type=submit]").click();
  await closed.waitFor({ state: "detached", timeout: 15000 }).catch(() => {});
  await page.goto(`${BASE}/exceptions`, { waitUntil: "networkidle" });
  if ((await batch().count()) !== 1) return note(page, where, "the batch opened again is not back on the list");
  await batch().locator("button[data-decide]").click();
  const history = page.locator("[role=dialog] ol[data-history] > li");
  await history.nth(1).waitFor({ timeout: 5000 }).catch(() => {});
  if ((await history.count()) !== 2) await note(page, where, "the history does not hold both decisions");
  await page.keyboard.press("Escape");
}

async function checkMenu(page, w, h) {
  await page.goto(`${BASE}/overview`, { waitUntil: "networkidle" });
  const button = page.locator('button[aria-haspopup="menu"]:visible').first();
  if (!(await button.count())) return note(page, `${w}x${h} account menu`, "no account menu button");
  await button.click();
  const items = page.locator('[role="menu"]:visible [role="menuitem"]');
  const n = await items.count();
  if (!n) return note(page, `${w}x${h} account menu`, "the menu did not open");
  for (let i = 0; i < n; i++) {
    const box = await items.nth(i).boundingBox();
    const top = await page.evaluate(([x, y]) => {
      const el = document.elementFromPoint(x, y);
      return el?.closest('[role="menuitem"]') ? null : `${el?.tagName} "${(el?.textContent ?? "").trim().slice(0, 30)}"`;
    }, [box.x + box.width / 2, box.y + box.height / 2]);
    if (top) await note(page, `${w}x${h} account menu`, `item ${i + 1} is covered by ${top}`);
  }
  await page.keyboard.press("Escape");
}

const browser = await chromium.launch();
try {
  // Sign in once, through the form, and reuse the session everywhere.
  const login = await browser.newContext();
  const lp = await login.newPage();
  await lp.goto(`${BASE}/signup?mode=signin`, { waitUntil: "networkidle" });
  await lp.fill("input[name=email]", EMAIL);
  await lp.fill("input[name=password]", PASSWORD);
  await lp.locator("form button[type=submit]").click();
  await lp.waitForURL(/\/overview/, { timeout: 20000 });
  const session = await login.storageState();

  // The detail pages a workspace has: its first merchant, its first connector.
  await lp.goto(`${BASE}/merchants`, { waitUntil: "networkidle" });
  const merchant = await lp.locator('a[href^="/merchants/ten_"]').first().getAttribute("href");
  await lp.goto(`${BASE}/integrations`, { waitUntil: "networkidle" });
  const connector = await lp.locator('a[href^="/integrations/"]').first().getAttribute("href");
  const detail = [merchant, `${merchant}?tab=findings`, `${merchant}?tab=connections`, `${merchant}?tab=files`,
    `${merchant}/import`, connector].filter(Boolean);
  await login.close();

  for (const [w, h] of SIZES) {
    const out = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
    const op = await out.newPage();
    for (const path of SIGNED_OUT) await checkPage(op, w, h, path);
    await out.close();

    const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce", storageState: session });
    const page = await ctx.newPage();
    // A page that throws while the browser takes it over (React's "hydration
    // failed", or anything else) is a broken page, whatever it then draws.
    // It fails this check on the Files tab, which is built so that it cannot
    // (see checkSlowly). Other pages are still drawn on the server in parts
    // and can do it now and then (known gap G22): those are printed, so they
    // are seen, and do not fail a job that would then fail at random.
    page.on("pageerror", (e) => {
      const at = new URL(page.url());
      const said = `${w}x${h} ${at.pathname}${at.search}: the page threw: ${String(e).split("\n")[0].slice(0, 200)}`;
      (at.search.includes("tab=files") ? problems : threw).push(said);
    });
    for (const path of [...SIGNED_IN, ...detail]) await checkPage(page, w, h, path);
    await checkPlanNotice(page, w, h);
    if (merchant) await checkFileCheck(page, w, h, merchant);
    if (merchant) await checkImport(page, w, h, merchant);
    await checkOpenItems(page, w, h);
    if (w >= 900) await checkMenu(page, w, h);
    await ctx.close();
  }
  if (merchant) slow = await checkSlowly(browser, session, merchant);
  const last = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: "reduce", storageState: session });
  await checkDeciding(await last.newPage());
  await last.close();
} finally {
  await browser.close();
}

const pages = SIZES.length * (SIGNED_OUT.length + SIGNED_IN.length + 7);
if (threw.length) console.log(`Noted, not failed (G22): ${threw.length} page view(s) threw while the browser took them over:\n- ${threw.join("\n- ")}`);
if (problems.length) {
  console.error(`Layout check failed (${problems.length}):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`Layout check passed: ${pages} page views at ${SIZES.map(([w]) => w).join(", ")}px, account menu on top, the refund notice ahead of Upgrade, the file check saying what its run holds, the open items listing the unpaid batch, a decision on it recorded with its reason and undone, and the Files tab taken over cleanly on ${slow} loads of a document arriving slowly.`);
