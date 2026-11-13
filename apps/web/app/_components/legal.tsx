/* The frame for the site's legal pages: the header and footer of the home
   page, one readable column, and a contents list that links each section. */
import { OkwanLogo, Wrap } from "./brand";

export const COMPANY = "Global Tech Startup LLC";
export const CONTACT_EMAIL = "info@globaltechstartup.com";
export const UPDATED = "November 4, 2026";

export type Part = { id: string; title: string; body: React.ReactNode };

export function LegalPage({ title, intro, parts, updated = UPDATED }: { title: string; intro: React.ReactNode; parts: Part[]; updated?: string }) {
  return (
    <>
      <header className="border-b border-line/80 bg-paper">
        {/* Four links and the logo do not fit one line on a phone: the
            links drop below it there instead of running off the edge. */}
        <Wrap className="flex min-h-16 flex-wrap items-center justify-between gap-x-6 gap-y-2 py-3">
          <a href="/" aria-label="Okwan home"><OkwanLogo width={96} /></a>
          <nav className="flex flex-wrap items-center gap-x-6 gap-y-1 text-[14.5px] text-ink-soft" aria-label="Policies">
            <a className="hover:text-ink aria-[current=page]:text-ink" href="/privacy" aria-current={title.startsWith("Privacy") ? "page" : undefined}>Privacy</a>
            <a className="hover:text-ink aria-[current=page]:text-ink" href="/terms" aria-current={title.startsWith("Terms") ? "page" : undefined}>Terms</a>
            <a className="hover:text-ink aria-[current=page]:text-ink" href="/refunds" aria-current={title.startsWith("Refunds") ? "page" : undefined}>Refunds</a>
            <a className="hover:text-ink aria-[current=page]:text-ink" href="/security" aria-current={title.startsWith("Security") ? "page" : undefined}>Security</a>
          </nav>
        </Wrap>
      </header>
      <main id="top" className="bg-paper">
        <Wrap className="py-14 lg:py-20">
          <div className="mx-auto max-w-[760px]">
            <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-ink-muted">Last updated {updated}</p>
            <h1 className="mt-3 text-[40px] font-semibold leading-[1.1] tracking-[-0.03em] text-ink sm:text-[48px]">{title}</h1>
            <div className="legal mt-6 text-[17px] leading-relaxed text-ink-soft">{intro}</div>
            <nav aria-label="Contents" className="mt-10 rounded-2xl border border-line bg-mist p-6">
              <p className="text-[13px] font-medium text-ink">Contents</p>
              <ol className="mt-3 grid gap-x-8 gap-y-1.5 text-[15px] sm:grid-cols-2">
                {parts.map((p, i) => (
                  <li key={p.id}>
                    <a className="text-ink-soft hover:text-ink hover:underline" href={`#${p.id}`}>{i + 1}. {p.title}</a>
                  </li>
                ))}
              </ol>
            </nav>
            <div className="legal mt-12 space-y-12 text-[16px] leading-relaxed text-ink-soft">
              {parts.map((p, i) => (
                <section key={p.id} id={p.id} aria-labelledby={`${p.id}-title`} className="scroll-mt-6">
                  <h2 id={`${p.id}-title`} className="text-[22px] font-semibold tracking-[-0.01em] text-ink">{i + 1}. {p.title}</h2>
                  <div className="mt-4 space-y-4">{p.body}</div>
                </section>
              ))}
            </div>
          </div>
        </Wrap>
      </main>
      <footer className="border-t border-night-line bg-night text-night-soft">
        <Wrap className="flex flex-wrap items-center justify-between gap-4 py-8 text-[14px]">
          <p>© 2026 {COMPANY}</p>
          <nav className="flex flex-wrap gap-x-6 gap-y-2" aria-label="Footer">
            <a className="hover:text-white" href="/">Home</a>
            <a className="hover:text-white" href="/privacy">Privacy</a>
            <a className="hover:text-white" href="/terms">Terms</a>
            <a className="hover:text-white" href="/refunds">Refunds</a>
            <a className="hover:text-white" href="/security">Security</a>
            <a className="hover:text-white" href={`mailto:${CONTACT_EMAIL}`}>Contact</a>
          </nav>
        </Wrap>
      </footer>
    </>
  );
}

/** A two-column table that scrolls inside its own box on a phone. */
export function Facts({ head, rows }: { head: [string, string] | [string, string, string]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-white" role="region" aria-label={head.join(", ")} tabIndex={0}>
      <table className="w-full min-w-[520px] text-left text-[15px]">
        <thead>
          <tr>{head.map((h) => <th key={h} scope="col" className="border-b border-line px-4 py-2.5 text-[13px] font-medium text-ink">{h}</th>)}</tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r) => (
            <tr key={r[0]}>{r.map((c, i) => <td key={i} className={`px-4 py-2.5 align-top ${i === 0 ? "font-medium text-ink" : ""}`}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Mail() {
  return <a className="font-medium text-ink underline underline-offset-4" href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>;
}
