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
const SIGNED_OUT = ["/signup?mode=signin", "/signup", "/verify?token=layout-check"];
const SIGNED_IN = [
  "/overview", "/overview?days=all", "/findings", "/merchants", "/integrations", "/agents",
  "/settings", "/settings?tab=plan", "/settings?tab=keys", "/settings?tab=security", "/ops",
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
  const detail = [merchant, `${merchant}?tab=findings`, `${merchant}?tab=connections`, connector].filter(Boolean);
  await login.close();

  for (const [w, h] of SIZES) {
    const out = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
    const op = await out.newPage();
    for (const path of SIGNED_OUT) await checkPage(op, w, h, path);
    await out.close();

    const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce", storageState: session });
    const page = await ctx.newPage();
    for (const path of [...SIGNED_IN, ...detail]) await checkPage(page, w, h, path);
    if (w >= 900) await checkMenu(page, w, h);
    await ctx.close();
  }
} finally {
  await browser.close();
}

const pages = SIZES.length * (SIGNED_OUT.length + SIGNED_IN.length + 4);
if (problems.length) {
  console.error(`Layout check failed (${problems.length}):\n- ${problems.join("\n- ")}`);
  process.exit(1);
}
console.log(`Layout check passed: ${pages} page views at ${SIZES.map(([w]) => w).join(", ")}px, account menu on top.`);
