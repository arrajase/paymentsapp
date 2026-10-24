import Link from "next/link";

import { signOut } from "@/app/actions";
import { Badge } from "@/components/ui";
import { getMe, getSystem } from "@/lib/api";
import { ROLE_LABELS } from "@/lib/format";

const NAV = [
  { href: "/plugins", label: "Plugins" },
  { href: "/approvals", label: "Approvals" },
  { href: "/audit", label: "Audit log" },
];

export default async function ConsoleLayout({ children }: LayoutProps<"/">) {
  const [me, system] = await Promise.all([getMe(), getSystem()]);
  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-zinc-200 bg-white">
        <div className="border-b border-zinc-200 px-5 py-4">
          <p className="text-xs font-semibold uppercase tracking-widest text-indigo-600">paymentsapp</p>
          <p className="font-semibold">Admin</p>
          <div className="mt-2">
            <Badge tone={system.environment === "production" ? "red" : "blue"}>{system.environment}</Badge>
          </div>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="block rounded-md px-3 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100"
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="border-t border-zinc-200 p-4">
          <p className="truncate text-sm font-medium" title={me.login_id}>
            {me.display_name}
          </p>
          <p className="truncate text-xs text-zinc-500">{me.login_id}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {me.roles.map((role) => (
              <Badge key={role}>{ROLE_LABELS[role] ?? role}</Badge>
            ))}
          </div>
          <form action={signOut} className="mt-3">
            <button type="submit" className="btn-secondary w-full">
              Sign out
            </button>
          </form>
        </div>
      </aside>
      <main className="flex-1 overflow-x-auto p-8">
        <div className="mx-auto max-w-5xl">{children}</div>
      </main>
    </div>
  );
}
