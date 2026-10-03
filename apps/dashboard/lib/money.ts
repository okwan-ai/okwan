/**
 * Integer minor units to a display string, without passing through a
 * float: the whole and fractional parts are split with BigInt, and Intl
 * formats only the whole part. The exponent comes from Intl's own
 * currency data (JPY 0, USD 2, KWD 3).
 */
export function formatMinor(minor: number | null | undefined, currency?: string | null): string {
  if (minor === null || minor === undefined || !Number.isInteger(minor)) return "—";
  const code = (currency ?? "").toUpperCase();
  let whole: Intl.NumberFormat;
  let digits = 2;
  try {
    digits = new Intl.NumberFormat("en-US", { style: "currency", currency: code })
      .resolvedOptions().maximumFractionDigits ?? 2;
    whole = new Intl.NumberFormat("en-US", {
      style: "currency", currency: code, minimumFractionDigits: 0, maximumFractionDigits: 0,
    });
  } catch {
    whole = new Intl.NumberFormat("en-US");
  }
  const value = BigInt(minor);
  const abs = value < BigInt(0) ? -value : value;
  const base = BigInt(10) ** BigInt(digits);
  const frac = digits ? `.${(abs % base).toString().padStart(digits, "0")}` : "";
  const head = whole.format(abs / base);
  const tail = whole.resolvedOptions().style === "currency" || !code ? "" : ` ${code}`;
  return `${value < BigInt(0) ? "−" : ""}${head}${frac}${tail}`;
}
