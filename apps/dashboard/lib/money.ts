/**
 * Integer minor units to a display string, without passing through a
 * float: the whole and fractional parts are split with BigInt, and Intl
 * formats only the whole part (symbol and grouping).
 *
 * The exponent is the API's, not Intl's. The API builds every minor-unit
 * integer with okwan_core/currency.py: exponent 0 for the currencies below,
 * 2 for every other. Intl disagrees for some (KWD 3, BIF 0, ...), and
 * formatting with Intl's exponent would show those amounts 10x or 100x off.
 * This mirrors ZERO_DECIMAL_CURRENCIES; keep the two in step.
 */
const ZERO_DECIMAL = new Set(["XOF", "XAF", "JPY", "KRW", "VND", "CLP", "ISK", "PYG", "RWF", "UGX", "VUV", "XPF"]);

export function exponent(currency?: string | null): number {
  return ZERO_DECIMAL.has((currency ?? "").toUpperCase()) ? 0 : 2;
}

export function formatMinor(minor: number | null | undefined, currency?: string | null): string {
  if (minor === null || minor === undefined || !Number.isInteger(minor)) return "—";
  const code = (currency ?? "").toUpperCase();
  let whole: Intl.NumberFormat;
  const digits = exponent(code);
  try {
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

/** Integer minor units to a plain decimal string ("999.00", "-12.50") at
 * the API's exponent, for CSV and other machine-read output. BigInt
 * throughout, like formatMinor. */
export function minorToDecimal(minor: number | null | undefined, currency?: string | null): string {
  if (minor === null || minor === undefined || !Number.isInteger(minor)) return "";
  const digits = exponent(currency);
  const value = BigInt(minor);
  const abs = value < BigInt(0) ? -value : value;
  const base = BigInt(10) ** BigInt(digits);
  const frac = digits ? `.${(abs % base).toString().padStart(digits, "0")}` : "";
  return `${value < BigInt(0) ? "-" : ""}${(abs / base).toString()}${frac}`;
}
