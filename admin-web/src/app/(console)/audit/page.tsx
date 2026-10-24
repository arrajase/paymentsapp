import { ErrorPanel, PageHeader } from "@/components/ui";
import { ApiError, getAudit } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export default async function AuditPage() {
  let entries;
  try {
    entries = await getAudit(100);
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }

  return (
    <>
      <PageHeader
        title="Audit log"
        description="The latest 100 entries, newest first. Entries can't be edited or deleted, and each is chained to the one before it."
      />
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase text-zinc-500">
            <tr>
              <th className="px-4 py-2 font-medium">When</th>
              <th className="px-4 py-2 font-medium">Action</th>
              <th className="px-4 py-2 font-medium">Actor</th>
              <th className="px-4 py-2 font-medium">Resource</th>
              <th className="px-4 py-2 font-medium">Details</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100">
            {entries.map((entry) => (
              <tr key={entry.id} className="align-top">
                <td className="whitespace-nowrap px-4 py-2 text-zinc-600">{formatDateTime(entry.occurred_at)}</td>
                <td className="px-4 py-2 font-mono">{entry.action}</td>
                <td className="px-4 py-2">
                  <span className="text-zinc-500">{entry.actor_type}</span>
                  {entry.actor_id && <span className="block font-mono text-xs">{entry.actor_id}</span>}
                  {entry.ip && <span className="block text-xs text-zinc-500">{entry.ip}</span>}
                </td>
                <td className="px-4 py-2">
                  <span className="text-zinc-500">{entry.resource_type}</span>
                  {entry.resource_id && <span className="block font-mono text-xs">{entry.resource_id}</span>}
                </td>
                <td className="px-4 py-2">
                  {(entry.before || entry.after) && (
                    <details>
                      <summary className="cursor-pointer text-indigo-600">Show</summary>
                      {entry.before && (
                        <pre className="mt-2 max-w-md overflow-x-auto rounded bg-zinc-50 p-2 text-xs">
                          before: {JSON.stringify(entry.before, null, 2)}
                        </pre>
                      )}
                      {entry.after && (
                        <pre className="mt-2 max-w-md overflow-x-auto rounded bg-zinc-50 p-2 text-xs">
                          after: {JSON.stringify(entry.after, null, 2)}
                        </pre>
                      )}
                    </details>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
