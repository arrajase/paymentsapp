import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge, ErrorPanel, Notice, PageHeader } from "@/components/ui";
import { ApiError, getMe, getPlugins, getSystem } from "@/lib/api";
import { formatDateTime, hasRole } from "@/lib/format";

import { ProposeForm } from "./propose-form";

export default async function PluginPage({ params }: PageProps<"/plugins/[key]">) {
  const { key } = await params;
  let plugins, me, system;
  try {
    [plugins, me, system] = await Promise.all([getPlugins(), getMe(), getSystem()]);
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }
  const plugin = plugins.find((p) => p.key === key);
  if (!plugin) notFound();
  const canPropose = hasRole(me, "platform_admin");

  return (
    <>
      <p className="mb-2 text-sm">
        <Link href="/plugins" className="text-indigo-600 hover:underline">
          ← Plugins
        </Link>
      </p>
      <PageHeader title={plugin.key} description={plugin.description}>
        <Badge tone={plugin.enabled ? "green" : "grey"}>{plugin.enabled ? "enabled" : "disabled"}</Badge>
      </PageHeader>

      {plugin.pending_request_id && (
        <div className="mb-6">
          <Notice tone="warning">
            A change to this plugin is waiting for approval.{" "}
            <Link href="/approvals" className="font-medium underline">
              Review it
            </Link>
            . A new change can be proposed once it is decided.
          </Notice>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="card">
          <h2 className="font-semibold">Current settings</h2>
          <dl className="mt-4 space-y-3 text-sm">
            <Row label="Mode">
              {plugin.mode} <span className="muted">(allowed here: {plugin.allowed_modes.join(", ")})</span>
            </Row>
            <Row label="Serves">{plugin.interfaces.join(", ")}</Row>
            {plugin.config.map((field) => (
              <Row key={field.name} label={field.label}>
                <span className="font-mono">{field.value ?? "not set"}</span>
              </Row>
            ))}
            {plugin.secrets.map((secret) => (
              <Row key={secret.name} label={secret.label}>
                {secret.preview ? (
                  <>
                    <span className="font-mono">{secret.preview}</span>
                    <span className="muted"> · set {formatDateTime(secret.set_at)}</span>
                  </>
                ) : (
                  <Badge tone="amber">not set</Badge>
                )}
              </Row>
            ))}
            <Row label="Version">
              {plugin.version} <span className="muted">· updated {formatDateTime(plugin.updated_at)}</span>
            </Row>
          </dl>
          {plugin.key === "mockbank" && (
            <div className="mt-5 rounded-md bg-zinc-50 p-3 text-sm">
              <p className="font-medium">Webhook URL to register at the bank</p>
              <p className="mt-1 break-all font-mono text-xs">{system.webhook_url}</p>
              <p className="mt-1 muted">
                Set it with the mock bank&apos;s <span className="font-mono">set-webhook</span> command once this
                backend is reachable from the internet.
              </p>
            </div>
          )}
        </section>

        <section className="card">
          <h2 className="font-semibold">Propose a change</h2>
          {!canPropose ? (
            <p className="mt-3 muted">Only platform admins can propose changes.</p>
          ) : plugin.pending_request_id ? (
            <p className="mt-3 muted">Waiting for the current change to be decided.</p>
          ) : (
            <ProposeForm plugin={plugin} />
          )}
        </section>
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <dt className="text-zinc-500">{label}</dt>
      <dd className="col-span-2 break-all">{children}</dd>
    </div>
  );
}
