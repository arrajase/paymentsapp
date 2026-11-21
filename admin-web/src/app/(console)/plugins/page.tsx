import Link from "next/link";

import { Badge, ErrorPanel, PageHeader } from "@/components/ui";
import { ApiError, getPluginHealth, getPlugins } from "@/lib/api";
import { formatDateTime, healthSummary } from "@/lib/format";

export default async function PluginsPage() {
  let plugins, health;
  try {
    [plugins, health] = await Promise.all([getPlugins(), getPluginHealth()]);
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }

  return (
    <>
      <PageHeader
        title="Plugins"
        description="Partner connections the backend uses. Changes need a second admin's approval."
      />
      {plugins.length === 0 && (
        <p className="muted">
          No plugins yet. Run <code className="font-mono">uv run python -m paymentsapp.cli seed-plugins</code> in
          backend/.
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {plugins.map((plugin) => {
          const pluginHealth = health.find((h) => h.key === plugin.key);
          const summary = pluginHealth ? healthSummary(pluginHealth) : null;
          return (
            <Link
              key={plugin.key}
              href={`/plugins/${plugin.key}`}
              className="card block transition hover:border-indigo-300"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="font-mono text-base font-semibold">{plugin.key}</h2>
                  <p className="mt-1 text-sm text-zinc-600">{plugin.description}</p>
                </div>
                <Badge tone={plugin.enabled ? "green" : "grey"}>{plugin.enabled ? "enabled" : "disabled"}</Badge>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Badge tone="blue">mode: {plugin.mode}</Badge>
                {plugin.interfaces.map((i) => (
                  <Badge key={i}>{i}</Badge>
                ))}
                {plugin.pending_request_id && <Badge tone="amber">change waiting for approval</Badge>}
                {summary && plugin.enabled && <Badge tone={summary.tone}>health: {summary.label}</Badge>}
              </div>
              <p className="mt-4 text-xs text-zinc-500">
                Version {plugin.version} · updated {formatDateTime(plugin.updated_at)}
              </p>
            </Link>
          );
        })}
      </div>
    </>
  );
}
