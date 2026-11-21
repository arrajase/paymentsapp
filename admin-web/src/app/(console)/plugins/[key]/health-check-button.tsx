"use client";

import { useActionState } from "react";

import { checkPluginHealth, type FormState } from "@/app/actions";
import { SubmitButton } from "@/components/submit-button";
import { Notice } from "@/components/ui";

export function HealthCheckButton({ pluginKey }: { pluginKey: string }) {
  const [state, action] = useActionState<FormState, FormData>(checkPluginHealth.bind(null, pluginKey), {});
  return (
    <form action={action} className="mt-4 space-y-3">
      <SubmitButton variant="secondary" pendingText="Checking… (a sleeping bank can take up to a minute)">
        Check now
      </SubmitButton>
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {state.message && <Notice tone="success">{state.message}</Notice>}
    </form>
  );
}
