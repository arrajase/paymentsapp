import { notFound } from "next/navigation";

import { Badge, ErrorPanel, Notice, PageHeader } from "@/components/ui";
import { ApiError, getDevInbox, getSystem } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

import { AutoRefresh } from "./auto-refresh";

export default async function DevInboxPage() {
  let system, inbox;
  try {
    system = await getSystem();
    if (system.environment !== "development") notFound();
    inbox = await getDevInbox();
  } catch (error) {
    if (error instanceof ApiError) return <ErrorPanel message={error.message} />;
    throw error;
  }

  return (
    <>
      <AutoRefresh seconds={5} />
      <PageHeader
        title="Dev inbox"
        description="Development only. Messages the app would have sent by SMS or email to test customers, such as sign-in codes. Updates every 5 seconds; messages are kept for a day."
      />
      <div className="mb-5">
        {inbox.adb_found ? (
          <Notice tone="info">
            adb found: SMS are also pushed into the Android emulator when it is running.
          </Notice>
        ) : (
          <Notice tone="warning">
            adb not found, so SMS appear only here. After installing Android Studio, set ADB_PATH in backend/.env or
            add platform-tools to PATH, and codes will also arrive as an SMS in the emulator.
          </Notice>
        )}
      </div>

      {inbox.messages.length === 0 && (
        <p className="muted">No messages yet. Request a sign-in code from the app or from the backend&apos;s /docs page.</p>
      )}
      <div className="space-y-3">
        {inbox.messages.map((message) => {
          const code = message.purpose === "otp" ? message.body.match(/\b(\d{6})\b/)?.[1] : undefined;
          return (
            <article key={message.id} className="card">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={message.channel === "sms" ? "blue" : "grey"}>{message.channel}</Badge>
                  <Badge tone={message.purpose === "otp" ? "green" : "amber"}>{message.purpose.replace("_", " ")}</Badge>
                  <span className="font-mono text-sm">{message.to_address}</span>
                  {message.pushed_to_emulator && <Badge tone="green">delivered to emulator</Badge>}
                </div>
                <span className="text-xs text-zinc-500">{formatDateTime(message.created_at)}</span>
              </div>
              {code && <p className="mt-3 font-mono text-3xl font-semibold tracking-[0.3em]">{code}</p>}
              {message.subject && <p className="mt-2 text-sm font-medium">{message.subject}</p>}
              <p className="mt-2 text-sm text-zinc-600">{message.body}</p>
            </article>
          );
        })}
      </div>
    </>
  );
}
