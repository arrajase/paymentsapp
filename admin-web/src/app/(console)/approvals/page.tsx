import Link from "next/link";

import { ErrorPanel, PageHeader, StatusBadge } from "@/components/ui";
import { ApiError, getChangeRequests, getMe, getPlugins } from "@/lib/api";
import { formatDateTime, hasRole } from "@/lib/format";
import type { ChangeRequest, Plugin, RequestStatus } from "@/lib/types";

import { CancelForm, DecisionForm } from "./decision-forms";

const TABS: { status: RequestStatus | "ALL"; label: string }[] = [
  { status: "PENDING", label: "Waiting" },
  { status: "ALL", label: "All" },
];

export default async function ApprovalsPage({ searchParams }: PageProps<"/approvals">) {
  const { status: raw } = await searchParams;
  const tab = raw === "ALL" ? "ALL" : "PENDING";
  let requests, me, plugins;
  try {
    [requests, me, plugins] = await Promise.all([
      getChangeRequests(tab === "ALL" ? undefined : "PENDING"),
      getMe(),
      getPlugins(),
    ]);
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }
  const pluginsByKey = new Map(plugins.map((p) => [p.key, p]));

  return (
    <>
      <PageHeader
        title="Approvals"
        description="Every plugin change needs a second admin. Security approvers decide; the proposer can withdraw."
      />
      <div className="mb-5 flex gap-2">
        {TABS.map((t) => (
          <Link
            key={t.status}
            href={t.status === "PENDING" ? "/approvals" : "/approvals?status=ALL"}
            className={t.status === tab ? "btn-primary" : "btn-secondary"}
          >
            {t.label}
          </Link>
        ))}
      </div>

      {requests.length === 0 && (
        <p className="muted">{tab === "PENDING" ? "Nothing is waiting for approval." : "No change requests yet."}</p>
      )}

      <div className="space-y-4">
        {requests.map((request) => {
          const own = request.requested_by.id === me.id;
          const canDecide = request.status === "PENDING" && hasRole(me, "security_approver") && !own;
          const canCancel = request.status === "PENDING" && own;
          return (
            <article key={request.id} className="card">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="font-semibold">
                    <span className="text-zinc-500">{request.target_type}</span>{" "}
                    <span className="font-mono">{request.target_key}</span>
                  </h2>
                  <p className="mt-1 text-sm text-zinc-600">
                    Proposed by {request.requested_by.display_name} ({request.requested_by.login_id}) ·{" "}
                    {formatDateTime(request.requested_at)}
                  </p>
                </div>
                <StatusBadge status={request.status} />
              </div>

              <ChangeTable request={request} plugin={pluginsByKey.get(request.target_key)} />

              <p className="mt-4 text-sm">
                <span className="font-medium">Reason:</span> {request.reason}
              </p>

              {request.decided_at && (
                <p className="mt-2 text-sm text-zinc-600">
                  {request.status === "CANCELLED" ? "Withdrawn" : `${request.status.toLowerCase()} by `}
                  {request.decided_by && `${request.decided_by.display_name} (${request.decided_by.login_id})`} ·{" "}
                  {formatDateTime(request.decided_at)}
                  {request.decision_note && <> · “{request.decision_note}”</>}
                </p>
              )}

              {canDecide && <DecisionForm requestId={request.id} />}
              {canCancel && <CancelForm requestId={request.id} />}
              {request.status === "PENDING" && own && hasRole(me, "security_approver") && (
                <p className="mt-3 muted">You proposed this, so a different security approver must decide it.</p>
              )}
              {request.status === "PENDING" && !own && !hasRole(me, "security_approver") && (
                <p className="mt-3 muted">Only security approvers can decide this.</p>
              )}
            </article>
          );
        })}
      </div>
    </>
  );
}

function ChangeTable({ request, plugin }: { request: ChangeRequest; plugin?: Plugin }) {
  const rows: { label: string; from: string; to: string }[] = [];
  if (request.changes.enabled !== undefined) {
    rows.push({
      label: "Enabled",
      from: plugin ? String(plugin.enabled) : "?",
      to: String(request.changes.enabled),
    });
  }
  if (request.changes.mode) {
    rows.push({ label: "Mode", from: plugin?.mode ?? "?", to: request.changes.mode });
  }
  for (const [name, value] of Object.entries(request.changes.config ?? {})) {
    const field = plugin?.config.find((f) => f.name === name);
    rows.push({ label: field?.label ?? name, from: field?.value ?? "not set", to: value });
  }
  for (const [name, preview] of Object.entries(request.secrets)) {
    const secret = plugin?.secrets.find((s) => s.name === name);
    rows.push({ label: secret?.label ?? name, from: secret?.preview ?? "not set", to: `${preview} (new secret)` });
  }
  // Once decided, "from" shows today's value rather than the value at the time, so show only the new values.
  const decided = request.status !== "PENDING";
  return (
    <table className="mt-4 w-full text-left text-sm">
      <thead className="text-xs uppercase text-zinc-500">
        <tr>
          <th className="py-1 pr-4 font-medium">Setting</th>
          {!decided && <th className="py-1 pr-4 font-medium">Now</th>}
          <th className="py-1 font-medium">{decided ? "Proposed value" : "Would become"}</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-zinc-100">
        {rows.map((row) => (
          <tr key={row.label}>
            <td className="py-2 pr-4 text-zinc-600">{row.label}</td>
            {!decided && <td className="break-all py-2 pr-4 font-mono">{row.from}</td>}
            <td className="break-all py-2 font-mono font-medium">{row.to}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
