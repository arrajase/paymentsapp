import type { Admin, Role } from "@/lib/types";

const dateTime = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  dateStyle: "medium",
  timeStyle: "short",
});

/** Dates are shown in IST, the timezone staff work in. */
export function formatDateTime(iso: string | null | undefined): string {
  return iso ? `${dateTime.format(new Date(iso))} IST` : "—";
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
