import { Badge, ErrorPanel, PageHeader } from "@/components/ui";
import { ApiError, getMe, getReconciliation } from "@/lib/api";
import { formatDateTime, formatRupees, hasRole } from "@/lib/format";
import type { ReconciliationRun } from "@/lib/types";

import { RunForm } from "./run-form";

const ISSUE_LABELS: Record<string, string> = {
  balance_mismatch: "Escrow balance differs from the bank",
  bank_only: "Money moved at the bank with no ledger entry",
  ledger_only: "Ledger entry missing from the bank statement",
  ledger_out_of_balance: "Escrow doesn't equal wallets plus money in transit",
  bank_balance_unavailable: "Couldn't read the bank balance",
  statement_unavailable: "Couldn't read the bank statement",
};

export default async function ReconciliationPage() {
  let data, me;
  try {
    [data, me] = await Promise.all([getReconciliation(), getMe()]);
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }
  const canRun = hasRole(me, "operations") || hasRole(me, "platform_admin") || hasRole(me, "compliance");
  const { totals } = data;

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="The ledger against the bank's escrow account. The worker reconciles each day once it ends; run it any time here."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="card">
          <h2 className="font-semibold">Ledger now</h2>
          <dl className="mt-4 space-y-3 text-sm">
            <Row label="Escrow (at the bank)">{formatRupees(totals.escrow)}</Row>
            <Row label="Customer wallets">{formatRupees(totals.wallets)}</Row>
            <Row label="In transit">{formatRupees(totals.in_transit)}</Row>
            <Row label="Escrow = wallets + in transit">
              {totals.escrow === totals.wallets + totals.in_transit ? (
                <Badge tone="green">holds</Badge>
              ) : (
                <Badge tone="red">does not hold</Badge>
              )}
            </Row>
          </dl>
        </section>
        <section className="card">
          <h2 className="font-semibold">Reconcile</h2>
          {canRun ? <RunForm /> : <p className="mt-3 muted">Operations, compliance and platform admins can run it.</p>}
        </section>
      </div>

      <h2 className="mt-8 mb-3 font-semibold">Runs</h2>
      {data.runs.length === 0 && <p className="muted">No runs yet.</p>}
      <div className="space-y-4">
        {data.runs.map((run) => (
          <RunCard key={run.id} run={run} />
        ))}
      </div>
    </>
  );
}

function RunCard({ run }: { run: ReconciliationRun }) {
  return (
    <section className="card">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="font-medium">{run.business_date}</span>
          <span className="muted">
            {" "}
            · {run.provider} · run {formatDateTime(run.ran_at)} by {run.ran_by === "worker" ? "the worker" : "an admin"}
          </span>
        </div>
        <Badge tone={run.clean ? "green" : "red"}>{run.clean ? "clean" : `${run.issues.length} issue(s)`}</Badge>
      </div>
      <p className="mt-2 text-sm">
        Bank {run.bank_balance === null ? "unavailable" : formatRupees(run.bank_balance)} · ledger{" "}
        {formatRupees(run.ledger_balance)} · {run.matched} matched
      </p>
      {run.issues.length > 0 && (
        <ul className="mt-3 space-y-2 text-sm">
          {run.issues.map((issue, i) => (
            <li key={i} className="rounded-md bg-rose-50 p-2">
              <span className="font-medium">{ISSUE_LABELS[String(issue.type)] ?? String(issue.type)}</span>
              <span className="block break-all font-mono text-xs text-zinc-600">
                {Object.entries(issue)
                  .filter(([key]) => key !== "type")
                  .map(([key, value]) =>
                    `${key}: ${typeof value === "number" && key !== "matched" ? formatRupees(value) : String(value)}`,
                  )
                  .join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      <dt className="text-zinc-500">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
