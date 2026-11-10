import { Badge, ErrorPanel, Notice, PageHeader } from "@/components/ui";
import { ApiError, getControls, getMe, getSystem } from "@/lib/api";
import { formatDateTime, hasRole, showValue } from "@/lib/format";
import type { Control } from "@/lib/types";

import { ControlForm } from "./control-form";

export default async function ControlsPage() {
  let controls, me, system;
  try {
    [controls, me, system] = await Promise.all([getControls(), getMe(), getSystem()]);
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }
  const canPropose = hasRole(me, "platform_admin");
  const security = controls.filter((c) => c.kind !== "feature");
  const features = controls.filter((c) => c.kind === "feature");

  return (
    <>
      <PageHeader
        title="Controls"
        description={`Security controls and feature switches for ${system.environment}. Only values the config files allow here can be chosen, and every change needs a second admin's approval.`}
      />
      {system.environment === "production" && (
        <div className="mb-6">
          <Notice tone="warning">
            In production, loosening a security control expires automatically and alerts the security team.
          </Notice>
        </div>
      )}
      <Section title="Security controls" controls={security} canPropose={canPropose} />
      <Section title="Feature switches" controls={features} canPropose={canPropose} />
    </>
  );
}

function Section({ title, controls, canPropose }: { title: string; controls: Control[]; canPropose: boolean }) {
  return (
    <section className="mb-8">
      <h2 className="mb-3 text-lg font-semibold">{title}</h2>
      <div className="card divide-y divide-zinc-100 p-0">
        {controls.map((control) => (
          <div key={control.key} className="grid gap-4 p-5 md:grid-cols-5">
            <div className="md:col-span-2">
              <p className="font-mono text-sm font-semibold">{control.key}</p>
              <p className="mt-1 text-sm text-zinc-600">{control.description}</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {control.client && <Badge>sent to the app</Badge>}
                {control.pending_request_id && <Badge tone="amber">change waiting for approval</Badge>}
              </div>
            </div>
            <div className="text-sm">
              <p className="muted">Now</p>
              <p className="mt-1">
                <ValueBadge control={control} />
              </p>
              <p className="mt-1 text-xs text-zinc-500">
                {control.source === "default" ? "default" : control.source}
                {control.kind === "number" && control.bounds && ` · allowed ${control.bounds[0]}–${control.bounds[1]}`}
              </p>
              {control.expires_at && (
                <p className="mt-1 text-xs text-amber-700">
                  Loosened: returns to {showValue(control.revert_value)} at {formatDateTime(control.expires_at)}
                </p>
              )}
            </div>
            <div className="md:col-span-2">
              {!canPropose ? (
                <p className="muted">Only platform admins can propose changes.</p>
              ) : control.pending_request_id ? (
                <p className="muted">Waiting for the current change to be decided on the Approvals page.</p>
              ) : control.kind !== "number" && control.allowed.length < 2 ? (
                <p className="muted">Fixed in this environment.</p>
              ) : (
                <ControlForm control={control} />
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ValueBadge({ control }: { control: Control }) {
  const value = control.value;
  const tone =
    value === "enforce" || value === true
      ? "green"
      : value === "monitor"
        ? "amber"
        : value === "off" || value === false
          ? "grey"
          : "blue";
  return <Badge tone={tone}>{showValue(value)}</Badge>;
}
