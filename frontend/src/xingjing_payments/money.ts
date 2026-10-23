export function fractionDigits(currency: string): number | undefined {
  try { return /^[A-Z]{3}$/.test(currency) ? new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits : undefined; }
  catch { return; }
}
export function minorDecimal(value: string, currency: string): string {
  const digits = fractionDigits(currency);
  if (!/^-?\d+$/.test(value) || digits === undefined) return value;
  const negative = value.startsWith("-"), raw = value.replace(/^-/, "").replace(/^0+(?=\d)/, "").padStart(digits + 1, "0");
  return `${negative ? "-" : ""}${digits ? `${raw.slice(0, -digits)}.${raw.slice(-digits)}` : raw}`;
}
export function parseMinor(value: string, currency: string): string | undefined {
  const digits = fractionDigits(currency);
  if (digits === undefined || !/^\d+(?:\.\d+)?$/.test(value.trim())) return;
  const [whole, fraction = ""] = value.trim().split(".");
  if (fraction.length > digits) return;
  return BigInt(`${whole}${fraction.padEnd(digits, "0")}`).toString();
}
export function money(value: string | null | undefined, currency: string | null | undefined, locale: string): string {
  if (value === null || value === undefined || !currency || !/^-?\d+$/.test(value)) return "—";
  const decimal = minorDecimal(value, currency);
  const [whole, fraction] = decimal.split(".");
  const negative = value.startsWith("-") && BigInt(value) !== 0n;
  const group = `${negative ? "-" : ""}${new Intl.NumberFormat(locale).format(BigInt(whole.replace(/^-/, "")))}`;
  const separator = new Intl.NumberFormat(locale).formatToParts(1.1).find((part) => part.type === "decimal")?.value ?? ".";
  return `${currency} ${group}${fraction ? `${separator}${fraction}` : ""}`;
}
export function share(part: string, total: string): number {
  if (!/^\d+$/.test(part) || !/^\d+$/.test(total) || BigInt(total) === 0n) return 0;
  return Number(BigInt(part) * 10000n / BigInt(total)) / 100;
}
export function checkoutUrl(raw: string | null | undefined): string | undefined {
  if (!raw) return;
  try { const value = new URL(raw); return value.protocol === "https:" && !value.username && !value.password ? value.href : undefined; }
  catch { return; }
}
