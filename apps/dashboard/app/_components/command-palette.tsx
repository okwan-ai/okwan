"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { OUTCOME_LABEL, OUTCOME_MARK } from "@/lib/finding";
import { useTabFindings, useTabResults } from "@/lib/tab-results";
import type { MerchantLink } from "./sidebar";
import { useDialog } from "./ui/dialog";
import { IconSearch } from "./ui/icons";

type Command = { id: string; group: string; label: string; hint?: string; href: string; keywords?: string };

const PAGES: Command[] = [
  { id: "p-overview", group: "Pages", label: "Overview", href: "/overview", keywords: "home dashboard verdict" },
  { id: "p-findings", group: "Pages", label: "Findings", href: "/findings", keywords: "collected twice refunds issues" },
  { id: "p-twice", group: "Pages", label: "Findings: collected twice", href: "/findings?outcome=collected_twice", keywords: "double refund owed" },
  { id: "p-merchants", group: "Pages", label: "Merchants", href: "/merchants", keywords: "tenants stores" },
  { id: "p-keys", group: "Pages", label: "API keys", href: "/key", keywords: "token secret" },
  { id: "p-mcp", group: "Pages", label: "MCP for agents", href: "/mcp", keywords: "claude agent tools" },
  { id: "p-rails", group: "Pages", label: "Your own rails", href: "/connections", keywords: "connections credentials" },
  { id: "a-add", group: "Actions", label: "Add a merchant", href: "/merchants?add=1", keywords: "new create" },
];

/** Opens the palette from anywhere (the sidebar's Search button). */
export function openPalette() {
  window.dispatchEvent(new Event("okwan:palette"));
}

/**
 * ⌘K / Ctrl-K (or "/" outside a field): jump to a page, a merchant, or a
 * merchant's tab. Navigation only, so nothing here starts a metered run.
 * An ARIA combobox: the input owns a listbox, arrows move the active
 * option, Enter follows it, Escape closes.
 */
export function CommandPalette({ merchants }: { merchants: MerchantLink[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const panel = useDialog(open, () => setOpen(false));
  const results = useTabResults();
  const seen = useTabFindings();
  const list = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName));
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing && !open)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("okwan:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("okwan:palette", onOpen);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => [
    ...PAGES,
    ...merchants.flatMap((m) => {
      const d = results[m.id];
      const hint = !d ? undefined : !d.ok ? "couldn't run" : d.twice ? `×2 · ${d.open} findings` : d.open ? `${d.open} findings` : "all paid once";
      const base = `/merchants/${encodeURIComponent(m.id)}`;
      return [
        { id: `m-${m.id}`, group: "Merchants", label: m.name, hint, href: base, keywords: m.id },
        { id: `m-${m.id}-c`, group: "Merchant tabs", label: `${m.name}: Connections`, href: `${base}?tab=connections`, keywords: `${m.id} rails credentials` },
        { id: `m-${m.id}-k`, group: "Merchant tabs", label: `${m.name}: API keys`, href: `${base}?tab=keys`, keywords: `${m.id} key mcp` },
      ];
    }),
  ], [merchants, results]);

  // Orders: findings this tab has seen, matched on the order number.
  const orders = useMemo<Command[]>(() => {
    const term = q.trim().toLowerCase().replace(/^#/, "");
    if (!/\d/.test(term)) return [];
    return Object.values(seen).flat()
      .filter((f) => f.order.toLowerCase().replace(/^#/, "").includes(term))
      .slice(0, 8)
      .map((f) => ({
        id: `o-${f.merchantId}-${f.order}`,
        group: "Orders",
        label: `${f.order} · ${f.merchantName}`,
        hint: `${OUTCOME_MARK[f.outcome] ?? ""} ${OUTCOME_LABEL[f.outcome] ?? f.outcome}${f.stake ? ` · ${f.stake}` : ""}`,
        href: `/merchants/${encodeURIComponent(f.merchantId)}?order=${encodeURIComponent(f.order)}`,
      }));
  }, [seen, q]);

  const shown = useMemo(() => {
    const terms = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const hits = commands.filter((c) => {
      const hay = `${c.label} ${c.keywords ?? ""}`.toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
    // Without a query, merchant tabs would crowd the list; show them on search.
    return [...orders, ...(terms.length ? hits : hits.filter((c) => c.group !== "Merchant tabs"))].slice(0, 40);
  }, [commands, orders, q]);

  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  if (!open) return null;

  function go(c: Command | undefined) {
    if (!c) return;
    setOpen(false);
    router.push(c.href);
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center px-4 pt-[min(12vh,5rem)]">
      <div className="absolute inset-0 bg-navy/40" onClick={() => setOpen(false)} aria-hidden />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Search pages and merchants"
        className="relative flex max-h-[min(70vh,560px)] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-xl"
      >
        <div className="flex items-center gap-2 border-b border-line px-4 focus-within:shadow-[inset_0_-2px_0_var(--color-ink)]">
          <IconSearch className="shrink-0 text-ink-soft" />
          <input
            data-autofocus
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={shown[active] ? `palette-${shown[active].id}` : undefined}
            aria-autocomplete="list"
            aria-label="Search"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(i + 1, shown.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); go(shown[active]); }
            }}
            placeholder="Search pages, merchants, orders…"
            className="min-h-13 flex-1 bg-transparent py-3 text-sm outline-none focus-visible:outline-none"
          />
          <kbd className="rounded border border-line px-1.5 font-mono text-[11px] text-ink-soft">esc</kbd>
        </div>
        <ul id="palette-list" ref={list} role="listbox" aria-label="Results" className="overflow-y-auto py-2">
          {shown.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-ink-soft">
              Nothing matches “{q}”.{/\d/.test(q) ? " Orders are searchable once Overview or Findings has loaded in this tab." : ""}
            </li>
          )}
          {shown.map((c, i) => (
            <li key={c.id} role="presentation">
              {(i === 0 || shown[i - 1].group !== c.group) && (
                <p role="presentation" className="px-4 pt-2 pb-1 text-[11px] font-medium tracking-wide text-ink-soft uppercase">{c.group}</p>
              )}
              <div
                id={`palette-${c.id}`}
                role="option"
                aria-selected={i === active}
                data-index={i}
                onMouseMove={() => setActive(i)}
                onClick={() => go(c)}
                className={`mx-2 flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-lg px-3 text-sm ${i === active ? "bg-ink/[0.06]" : ""}`}
              >
                <span className="truncate">{c.label}</span>
                {c.hint && <span className="shrink-0 text-xs text-ink-soft">{c.hint}</span>}
              </div>
            </li>
          ))}
        </ul>
        <p className="border-t border-line px-4 py-2 text-[11px] text-ink-soft">
          <kbd className="font-mono">↑↓</kbd> to move · <kbd className="font-mono">↵</kbd> to open · opening a page never runs a check
        </p>
      </div>
    </div>
  );
}
