import Link from "next/link";
import type { ComponentProps } from "react";

type Variant = "primary" | "secondary" | "ghost";

/** Primary: ink fill, white text (§2.3), for the one main action on a view.
 *  Secondary: a hairline on white. Ghost: text only, for low-weight actions.
 *  Yellow is never a button: it means "two systems disagree" (§2.1).
 *  Every size keeps a 44px touch target. */
const VARIANT: Record<Variant, string> = {
  primary: "bg-ink text-white shadow-[0_1px_2px_rgba(0,0,0,0.12)] hover:bg-[#262626]",
  secondary: "border border-ink/20 bg-surface text-ink shadow-[0_1px_2px_rgba(0,0,0,0.05)] hover:border-ink/50",
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
