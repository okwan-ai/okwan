import { railLabel } from "@/lib/finding";
import { BrandMark } from "./ui/brand-mark";

/** Connected rails as compact chips: ● every field stored, ◐ some. */
export function RailChips({ ready, partial, known = true }: { ready: string[]; partial: string[]; known?: boolean }) {
  if (!known) return <span className="text-xs text-ink-soft">Connections unavailable</span>;
  if (ready.length + partial.length === 0) return <span className="text-xs text-ink-soft">No rails yet</span>;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Rails">
      {ready.map((name) => (
        <li key={name} className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-1.5 py-0.5 text-xs">
          <span aria-hidden className="text-ok">●</span>
          <BrandMark name={name} label={railLabel(name)} size={12} />
          {railLabel(name)}
        </li>
      ))}
      {partial.map((name) => (
        <li key={name} className="inline-flex items-center gap-1 rounded-md border border-dashed border-line px-1.5 py-0.5 text-xs text-ink-soft">
          <span aria-hidden>◐</span>
          <BrandMark name={name} label={railLabel(name)} size={12} />
          {railLabel(name)} <span className="sr-only">(partly configured)</span>
          <span aria-hidden>· partial</span>
        </li>
      ))}
    </ul>
  );
}
