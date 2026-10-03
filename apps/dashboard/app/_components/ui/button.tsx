import Link from "next/link";
import type { ComponentProps } from "react";

type Variant = "primary" | "secondary" | "ghost";

/** Primary: volt fill, ink text (§2.3), for the one main action on a view.
 *  Secondary: outlined ink. Ghost: text only, for low-weight actions.
 *  Every size keeps a 44px touch target. */
const VARIANT: Record<Variant, string> = {
  primary: "bg-volt text-ink hover:bg-volt-deep",
  secondary: "border border-ink/80 bg-surface text-ink hover:bg-canvas",
  ghost: "text-ink-soft hover:bg-ink/5 hover:text-ink",
};

export function buttonClass(variant: Variant = "secondary", extra = ""): string {
  return [
    "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium",
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
