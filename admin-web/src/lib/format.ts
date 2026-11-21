import type { Admin, PluginHealth, Role } from "@/lib/types";

const dateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  dateStyle: "medium",
  timeStyle: "short",
});

/** Dates are shown in IST, the timezone staff work in. */
export function formatDateTime(iso: string | null | undefined): string {
  return iso ? `${dateTime.format(new Date(iso))} IST` : "—";
}

/** How a control value reads on screen: feature switches as on/off, everything else as is. */
export function showValue(value: boolean | number | string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "on" : "off";
  return String(value);
}

export function hasRole(admin: Admin, role: Role): boolean {
  return admin.roles.includes(role);
}

export const ROLE_LABELS: Record<Role, string> = {
  viewer: "Viewer",
  platform_admin: "Platform admin",
  security_approver: "Security approver",
  operations: "Operations",
  compliance: "Compliance",
  support: "Support",
};

const grouping = new Intl.NumberFormat("en-IN");

/** Paise to rupees with Indian digit grouping: 105050 -> "₹1,050.50". */
export function formatRupees(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const abs = Math.abs(paise);
  const part = abs % 100;
  return `${sign}₹${grouping.format(Math.floor(abs / 100))}${part ? `.${String(part).padStart(2, "0")}` : ""}`;
}

/** One-line health for a plugin, from the last check and this backend's circuit breaker. */
export function healthSummary(health: PluginHealth): { tone: "green" | "red" | "amber" | "grey"; label: string } {
  if (!health.enabled) return { tone: "grey", label: "disabled" };
  if (!health.remote) return { tone: "green", label: "runs in the backend" };
  if (health.circuit === "open") return { tone: "red", label: "failing: calls paused" };
  if (!health.last_check) return { tone: "grey", label: "not checked yet" };
  return health.last_check.ok ? { tone: "green", label: "healthy" } : { tone: "red", label: "last check failed" };
}
