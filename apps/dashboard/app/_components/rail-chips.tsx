import type { MerchantRails } from "@/lib/merchants";

export function RailChips({ m }: { m: MerchantRails }) {
  return (
    <div className="flex flex-wrap gap-2">
      {!m.known && <span className="text-xs text-ink-soft">Connections unavailable</span>}
      {m.known && m.ready.length + m.partial.length === 0 && (
        <span className="text-xs text-ink-soft">No connectors configured</span>
      )}
      {m.ready.map((name) => (
        <span key={name} className="rounded-full bg-ink px-3 py-1 text-xs font-medium capitalize text-canvas">
          {name}
        </span>
      ))}
      {m.partial.map((name) => (
        <span key={name} className="rounded-full border border-line px-3 py-1 text-xs capitalize text-ink-soft">
          {name} · partial
        </span>
      ))}
    </div>
  );
}
