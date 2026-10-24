import type { ReactNode } from "react";

const BADGE_TONES = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  amber: "bg-amber-50 text-amber-800 ring-amber-600/20",
  red: "bg-rose-50 text-rose-700 ring-rose-600/20",
  grey: "bg-zinc-100 text-zinc-700 ring-zinc-500/20",
  blue: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
} as const;

export function Badge({ tone = "grey", children }: { tone?: keyof typeof BADGE_TONES; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${BADGE_TONES[tone]}`}>
      {children}
    </span>
  );
}

export function PageHeader({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 muted">{description}</p>}
      </div>
      {children}
    </div>
  );
}

export function Notice({ tone, children }: { tone: "info" | "error" | "success" | "warning"; children: ReactNode }) {
  const tones = {
    info: "border-indigo-200 bg-indigo-50 text-indigo-900",
    error: "border-rose-200 bg-rose-50 text-rose-900",
    success: "border-emerald-200 bg-emerald-50 text-emerald-900",
    warning: "border-amber-200 bg-amber-50 text-amber-900",
  };
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`rounded-md border px-4 py-3 text-sm ${tones[tone]}`}>
      {children}
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone = { PENDING: "amber", APPROVED: "green", REJECTED: "red", CANCELLED: "grey" }[status] as
    | "amber"
    | "green"
    | "red"
    | "grey"
    | undefined;
  return <Badge tone={tone ?? "grey"}>{status.toLowerCase()}</Badge>;
}

export function ErrorPanel({ message }: { message: string }) {
  return (
    <Notice tone="error">
      <p className="font-medium">Couldn&apos;t load this page</p>
      <p className="mt-1">{message}</p>
    </Notice>
  );
}
