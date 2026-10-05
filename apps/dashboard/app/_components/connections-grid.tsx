"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { andList, groups, isComplete, missingForCheck } from "@/lib/connector-meta";
import { railLabel } from "@/lib/finding";
import { setSheetOpen, useTabTests } from "@/lib/tab-results";
import { ConnectorForm, type ConnectorView, isSuccess, type SheetResult } from "./connector-form";
import { ConnectorTile } from "./connector-tile";
import { BrandMark } from "./ui/brand-mark";
import { buttonClass } from "./ui/button";
import { Card, GRID } from "./ui/card";
import { SlideOver } from "./ui/dialog";
import { Section } from "./ui/page-header";

/** How long the sheet's pop flag may outlive its animation (the 420ms pop
 *  plus the disc's 180ms delay); under reduced motion it never ends. */
const SHEET_POP_MS = 700;

/**
 * A tenant's connections: the systems a check reads, then the rest, as logo
 * tiles; Connect or Manage opens the connect sheet. `tenantKey` names whose
 * tests and arrivals the tiles show (the merchant's id, or "self").
 *
 * With `fold` (a merchant's page), CheckReadiness leads while a system the
 * check reads is missing, and after a success the sheet offers the next
 * one. The connect moment itself (OKWAN_PROJECT.md §9 2026-10-05): the
 * sheet's logo tile turns from grey to colour and pops once when the live
 * read succeeds; the grid tile and the merchant header pop once each when
 * the sheet closes, because nothing behind an open sheet pops.
 */
export function ConnectionsGrid({ connectors, tenantId, tenantKey, fold = false }: {
  connectors: ConnectorView[];
  tenantId?: string;
  tenantKey: string;
  /** Show what a check still needs (a merchant's page). */
  fold?: boolean;
}) {
  // ?connect=paypal (from "Connect PayPal" anywhere) opens that sheet directly.
  const asked = useSearchParams().get("connect");
  const [managing, setManaging] = useState<string | null>(
    asked && connectors.some((c) => c.name === asked) ? asked : null,
  );
  // The open sheet's last save, its logo's pop, and whether it moved on
  // from a previous connector (its first field then takes focus).
  const [sheet, setSheet] = useState<SheetResult | null>(null);
  const [sheetPop, setSheetPop] = useState(false);
  const [chained, setChained] = useState(false);
  // Systems that arrived while this grid is up, counted as connected before
  // the refreshed props land.
  const [landed, setLanded] = useState<string[]>([]);

  const open = useCallback((name: string, chain = false) => {
    setManaging(name);
    setSheet(null);
    setSheetPop(false);
    setChained(chain);
  }, []);
  const root = useRef<HTMLDivElement>(null);
  const close = useCallback(() => {
    const was = managing;
    setManaging(null);
    setSheet(null);
    setSheetPop(false);
    // The sheet hands focus back to whatever opened it. When that is gone
    // (CheckReadiness leaves at 3 of 3), land on the tile it was about.
    requestAnimationFrame(() => {
      if (was && (document.activeElement === document.body || !document.activeElement)) {
        root.current?.querySelector<HTMLElement>(`[data-connector="${CSS.escape(was)}"]`)?.focus();
      }
    });
  }, [managing]);

  // Also when the link is followed from this tab (the grid stays mounted).
  // Used once: the param is dropped from the URL, so a later save (which
  // refreshes the page) never reopens a sheet the user has moved on from.
  useEffect(() => {
    if (!asked) return;
    if (connectors.some((c) => c.name === asked)) open(asked);
    // A task later, so on a fresh load this runs after the app router has
    // patched history (its effect runs after this child's): only then does
    // useSearchParams drop `connect`, and a later "Connect PayPal" link
    // (the header strip's) counts as a change and opens the sheet again.
    const t = setTimeout(() => {
      const u = new URL(window.location.href);
      u.searchParams.delete("connect");
      window.history.replaceState(null, "", u);
    }, 0);
    return () => clearTimeout(t);
  }, [asked]);

  // Nothing behind an open sheet pops; it plays when the sheet closes.
  const isOpen = managing !== null;
  useEffect(() => {
    setSheetOpen(tenantKey, isOpen);
  }, [tenantKey, isOpen]);
  useEffect(() => () => setSheetOpen(tenantKey, false), [tenantKey]);

  // Animationend never fires under reduced motion: clear the flag anyway.
  useEffect(() => {
    if (!sheetPop) return;
    const t = setTimeout(() => setSheetPop(false), SHEET_POP_MS);
    return () => clearTimeout(t);
  }, [sheetPop]);

  const tests = useTabTests();
  const { check, more } = groups(connectors);
  const current = connectors.find((c) => c.name === managing) ?? null;
  const missing = fold ? missingForCheck(connectors) : [];

  const onResult = (r: SheetResult) => {
    setSheet(r);
    if (r.arrived && current) {
      setLanded((l) => [...l, current.name]);
      setSheetPop(true);
    }
  };

  // The sheet's logo, in the sheet's state: colour once every field is
  // stored, the disc from this tab's last test, the pop on arrival.
  const sheetComplete = current ? isComplete(current) || Boolean(sheet?.complete) : false;
  const test = current ? tests[`${tenantKey}:${current.name}`] : undefined;
  const corner = test?.status === "failed" ? "alert" : test?.status === "rows" || test?.status === "empty" ? "ok" : undefined;

  // After a success the sheet's one volt moves on: the next system the check
  // still needs (fold grids only), else Done.
  const upNext = current && fold ? missingForCheck(connectors, [...landed, current.name])[0] : undefined;
  const next = sheet && isSuccess(sheet) ? (
    upNext ? (
      <button type="button" onClick={() => open(upNext.name, true)} className={buttonClass("primary", "w-full")}>
        Connect {railLabel(upNext.name)}
      </button>
    ) : (
      <button type="button" onClick={close} className={buttonClass("primary", "w-full")}>Done</button>
    )
  ) : null;

  const tile = (c: ConnectorView) => <ConnectorTile key={c.name} mode="tenant" c={c} tenantKey={tenantKey} onOpen={() => open(c.name)} />;

  return (
    <div ref={root} className="space-y-8">
      {missing.length > 0 && (
        // No logos here: the tiles below and the header strip carry them.
        <Card aria-label="What a check needs" className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3">
          <p className="text-sm font-medium">A check reads {andList(check.map((c) => railLabel(c.name)))}</p>
          <p className="text-xs text-ink-soft tabular-nums">
            {check.length - missing.length} of {check.length} connected
            <span className="sr-only">. Missing: {andList(missing.map((c) => railLabel(c.name)))}.</span>
          </p>
          <button type="button" onClick={() => open(missing[0].name)} className={buttonClass("primary", "ml-auto max-sm:w-full")}>
            Connect {railLabel(missing[0].name)}
          </button>
        </Card>
      )}
      {check.length > 0 && (
        <Section title="In the check" description="A check reads all three.">
          <ul className={GRID.tiles}>{check.map(tile)}</ul>
        </Section>
      )}
      {more.length > 0 && (
        <Section title="More connectors" description="Readable over REST, SQL and MCP. Not in the check yet.">
          <ul className={GRID.tiles}>{more.map(tile)}</ul>
        </Section>
      )}

      <SlideOver
        open={current !== null}
        onClose={close}
        title={current ? railLabel(current.name) : ""}
        icon={current && (
          <BrandMark
            name={current.name}
            label={railLabel(current.name)}
            tile
            size={40}
            muted={!sheetComplete}
            corner={corner}
            pop={sheetPop}
            onAnimationEnd={() => setSheetPop(false)}
          />
        )}
        description="We save it encrypted, then test it with one read."
      >
        {current && (
          <ConnectorForm
            key={current.name}
            c={current}
            tenantId={tenantId}
            tenantKey={tenantKey}
            onResult={onResult}
            next={next}
            autoFocus={chained}
          />
        )}
      </SlideOver>
    </div>
  );
}
