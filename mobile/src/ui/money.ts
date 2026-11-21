// Amounts travel as whole paise (plan M4). Rupees appear only on screen, and are turned into paise with string
// arithmetic, never floating point.

const grouping = new Intl.NumberFormat("en-IN");

/** 105050 -> "₹1,050.50"; 100000 -> "₹1,000". */
export function formatRupees(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const rupees = Math.floor(abs / 100);
  const part = abs % 100;
  return `${sign}₹${grouping.format(rupees)}${part ? `.${String(part).padStart(2, "0")}` : ""}`;
}

/** "500" -> 50000, "10.5" -> 1050, "10.55" -> 1055. Null for anything else, including more than 2 decimals. */
export function parseRupees(input: string): number | null {
  const match = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(input.trim().replace(/,/g, ""));
  if (!match) return null;
  const paise = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0") || "0");
  return paise > 0 ? paise : null;
}
