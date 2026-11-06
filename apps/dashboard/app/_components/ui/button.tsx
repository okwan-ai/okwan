import Link from "next/link";
import type { ComponentProps } from "react";

type Variant = "primary" | "secondary" | "ghost";

/** Primary: the purple gradient, white text, for the one main action on a
 *  view. Secondary: white with a hairline. Ghost: text only.
 *  Yellow is never a button: it means "two systems disagree".
 *  Every size keeps a 44px touch target. */
const VARIANT: Record<Variant, string> = {
  primary: "bg-violet text-white shadow-lift hover:brightness-110",
  secondary: "border border-line bg-surface text-ink shadow-[0_1px_2px_rgba(60,38,160,0.06)] hover:border-accent/40 hover:text-accent",
  ghost: "text-ink-soft hover:bg-accent-soft hover:text-accent",
};

export function buttonClass(variant: Variant = "secondary", extra = ""): string {
  return [
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold",
    "whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50",
    VARIANT[variant],
    extra,
  ].join(" ");
}

export function Button({
  variant = "secondary",
  className = "",
  type = "button",
  ...rest
}: ComponentProps<"button"> & { variant?: Variant }) {
  return <button type={type} className={buttonClass(variant, className)} {...rest} />;
}

export function ButtonLink({
  variant = "secondary",
  className = "",
  ...rest
}: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link className={buttonClass(variant, className)} {...rest} />;
}
