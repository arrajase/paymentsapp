import { Badge } from "@/components/ui";
import { formatDateTime, healthSummary } from "@/lib/format";
import type { PluginHealth } from "@/lib/types";

import { HealthCheckButton } from "./health-check-button";

const CIRCUIT_LABELS = {
  closed: "closed (calls go through)",
  open: "open (calls paused after repeated failures)",
  "half-open": "half-open (trying one call)",
} as const;

export function HealthPanel({ health }: { health: PluginHealth }) {
  const summary = healthSummary(health);
  const events = health.events;
  return (
    <section className="card">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold">Health</h2>
        <Badge tone={summary.tone}>{summary.label}</Badge>
      </div>
      {!health.remote ? (
        <p className="mt-3 muted">This plugin runs inside the backend, so there is no partner to reach.</p>
      ) : (
        <>
          <dl className="mt-4 space-y-3 text-sm">
            <Row label="Last check">
              {health.last_check ? (
                <>
                  {health.last_check.detail}
                  {health.last_check.latency_ms !== null && <span className="muted"> · {health.last_check.latency_ms} ms</span>}
                  <span className="muted"> · {formatDateTime(health.last_check.checked_at)}</span>
                </>
              ) : (
                <span className="muted">never</span>
              )}
            </Row>
            <Row label="Circuit breaker">
              {health.circuit ? CIRCUIT_LABELS[health.circuit] : <span className="muted">no calls since the backend started</span>}
            </Row>
            {events && (
              <>
                <Row label="Last webhook">{formatDateTime(events.last_webhook_at)}</Row>
                <Row label="Event poller">
                  {events.last_poll_at ? (
                    <>
                      last run {formatDateTime(events.last_poll_at)}
                      {events.last_poll_error ? (
                        <span className="block text-rose-700">failed: {events.last_poll_error}</span>
                      ) : (
                        <span className="muted"> · succeeded</span>
                      )}
                    </>
                  ) : (
                    <span className="muted">
                      not run yet: start the worker with <span className="font-mono">uv run python -m paymentsapp.worker</span>
                    </span>
                  )}
                </Row>
                <Row label="Events, last 24 hours">{events.received_24h}</Row>
              </>
            )}
          </dl>
          <HealthCheckButton pluginKey={health.key} />
        </>
      )}
    </section>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="col-span-2 break-words">{children}</dd>
    </div>
  );
}
