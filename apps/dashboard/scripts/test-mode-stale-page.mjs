// Test mode across two tabs: a change made on a page shown in Test mode
// lands on the live account (the attacker's pass on Test mode; the API half
// is tests/test_test_mode_attacks.py).
//
// FINDING. The mode is a cookie (lib/api.ts, MODE_COOKIE) that every route
// handler reads when a change arrives. Nothing in the change says which mode
// the page that sent it was showing, and every tab of a browser shares the
// cookie. So a person who opens Settings in Test mode, leaves Test mode in
// another tab, and then issues a key, adds a merchant or adds a webhook
// address from the first page (still under the yellow banner, still saying
// "In Test mode a key starts okw_test_ and reaches your sandbox and nothing
// live") gets a live admin key, a live merchant, and live events sent to the
// address they meant for testing.
//
// It sends exactly what the page's own code sends (issue-key.tsx,
// add-merchant.tsx, settings/webhooks.tsx, auth-form.tsx, test-mode.tsx), and
// asserts the safe behaviour: a change made from a page shown in Test mode
// lands in the sandbox or is refused. The checks marked HELD passed when this
// was written and stay as regression checks. Exits 1 on any FAIL.
//
// Fixed (lib/api.ts, PAGE_MODE_HEADER): every request a page makes to /api/
// now says the mode the page was shown in (test-mode.tsx, ModeStamp), as
// this script sends it from tab A, and the dashboard refuses one whose page
// disagrees with the browser's mode, and a change that does not say.
//
// Against a running dashboard and a freshly seeded API whose account has no
// sandbox yet (python scripts/ui_server.py):
//   BASE=http://127.0.0.1:3000 EMAIL=layout@okwan.test PASSWORD=… API=http://127.0.0.1:8000 \
//     node scripts/test-mode-stale-page.mjs

const BASE = (process.env.BASE ?? "http://127.0.0.1:3000").replace(/\/$/, "");
const API = (process.env.API ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const EMAIL = process.env.EMAIL ?? "layout@okwan.test";
const PASSWORD = process.env.PASSWORD;
if (!PASSWORD) throw new Error("PASSWORD is not set");

/** One browser's cookies: both tabs share them. */
const jar = new Map();

function keep(res) {
  for (const line of res.headers.getSetCookie()) {
    const [pair, ...attrs] = line.split(";");
    const at = pair.indexOf("=");
    const name = pair.slice(0, at).trim();
    const value = pair.slice(at + 1).trim();
    const gone = attrs.some((a) => /^\s*max-age=0\s*$/i.test(a))
      || attrs.some((a) => /^\s*expires=/i.test(a) && new Date(a.split("=")[1]) < new Date());
    if (gone || value === "") jar.delete(name);
    else jar.set(name, value);
  }
}

function cookie() {
  return [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
}

async function post(path, body, { origin = BASE, shown = "live" } = {}) {
  const headers = { "Content-Type": "application/json", Cookie: cookie() };
  if (origin) headers.Origin = origin;
  // What the page's ModeStamp adds: the mode the page was shown in.
  if (shown) headers["Okwan-Page-Mode"] = shown;
  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body), redirect: "manual" });
  keep(res);
  return { status: res.status, data: await res.json().catch(() => ({})) };
}

async function page(path) {
  const res = await fetch(`${BASE}${path}`, { headers: { Cookie: cookie() }, redirect: "manual" });
  keep(res);
  return { status: res.status, location: res.headers.get("location") ?? "", html: await res.text() };
}

const failures = [];
function check(ok, what) {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
}

// ── HELD: the switch itself ─────────────────────────────────────────────
check((await post("/api/mode", { mode: "test" })).status === 401 && !jar.has("okwan_mode"),
  "HELD: /api/mode without a session is refused and sets nothing");

const signin = await post("/api/signin", { email: EMAIL, password: PASSWORD });
if (signin.status !== 201) throw new Error(`sign-in: ${signin.status} ${JSON.stringify(signin.data)}`);

for (const origin of ["https://evil.example", null]) {
  const r = await post("/api/mode", { mode: "test" }, { origin });
  check(r.status === 403 && !jar.has("okwan_mode"), `HELD: /api/mode from ${origin ?? "no Origin"} is refused (${r.status})`);
}

// HELD: a mode cookie with no sandbox behind it (one written by hand, or
// left from a sandbox since removed) does not leave the browser stuck: the
// pages send it to sign-in, and signing in starts live again.
jar.set("okwan_mode", "test");
const stuck = await page("/overview");
check(stuck.status === 307 && stuck.location.includes("/signup?mode=signin"),
  `HELD: a mode cookie with no sandbox lands on sign-in (${stuck.status} ${stuck.location})`);
await post("/api/signin", { email: EMAIL, password: PASSWORD });
check(!jar.has("okwan_mode") && (await page("/overview")).status === 200,
  "HELD: signing in again clears the mode and the account opens live");

// ── FINDING: a change from a page shown in Test mode ───────────────────
const on = await post("/api/mode", { mode: "test" });
if (on.status !== 200 || jar.get("okwan_mode") !== "test") throw new Error(`Test mode on: ${on.status}`);

// Tab A: Settings, shown in Test mode.
const a = await page("/settings?tab=keys");
check(a.status === 200 && a.html.includes("Test mode.") && a.html.includes("In Test mode a key starts"),
  "tab A shows Settings in Test mode, with the banner and the test-key wording");

// Tab B: Leave Test mode (the banner's button there).
const off = await post("/api/mode", { mode: "live" });
if (off.status !== 200 || jar.has("okwan_mode")) throw new Error(`Test mode off: ${off.status}`);

// Back in tab A, still under the banner: Issue a key for "Your workspace", with admin access.
const key = await post("/api/key", { scope: "admin" }, { shown: "test" });
check(key.status !== 201 || String(key.data.secret ?? "").startsWith("okw_test_"),
  `the key issued from tab A is a test key, or refused (got ${key.status}, ${String(key.data.prefix ?? key.data.detail)})`);

// Add a merchant there.
const merchant = await post("/api/merchants", { name: `Test-only café ${Date.now()}` }, { shown: "test" });
check(merchant.status !== 201 || merchant.data.sandbox === true,
  `the merchant added from tab A is in the sandbox, or refused (got ${merchant.status}, sandbox=${merchant.data.sandbox})`);

// And a webhook address meant for testing.
const url = `https://test-receiver-${Date.now()}.example/okwan`;
const hook = await post("/api/webhooks", { url }, { shown: "test" });
const live = await page("/settings?tab=webhooks");
check(hook.status !== 201 || !live.html.includes(url),
  `the webhook address added from tab A is not one of the live account's (got ${hook.status}; on the live page: ${live.html.includes(url)})`);

// And a change that does not say which mode its page showed is refused.
const unsaid = await post("/api/merchants", { name: `Unsaid café ${Date.now()}` }, { shown: null });
check(unsaid.status === 409, `a change that does not say its page's mode is refused (got ${unsaid.status})`);
// The live page, in the mode the browser is in, still works.
const fine = await post("/api/merchants", { name: `Live café ${Date.now()}` }, { shown: "live" });
check(fine.status === 201 && fine.data.sandbox !== true, `a change from a page in the browser's mode lands there (got ${fine.status})`);

// ── HELD: signing out in Test mode ends the session on both sides ──────
await post("/api/mode", { mode: "test" });
const token = jar.get("okwan_session");
const out = await post("/api/signout", {});
check(out.status === 200 && !jar.has("okwan_session") && !jar.has("okwan_mode"),
  "HELD: signing out in Test mode clears the session and the mode");
for (const mode of [null, "test"]) {
  const headers = { Authorization: `Bearer ${token}` };
  if (mode) headers["Okwan-Mode"] = mode;
  const r = await fetch(`${API}/v1/tenants`, { headers });
  check(r.status === 401, `HELD: the signed-out session is refused by the API${mode ? " in Test mode" : ""} (${r.status})`);
}

if (failures.length) {
  console.log(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("\nevery check passed");
