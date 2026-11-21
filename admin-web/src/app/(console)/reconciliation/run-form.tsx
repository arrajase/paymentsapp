"use client";

import { useActionState } from "react";

import { runReconciliation, type FormState } from "@/app/actions";
import { SubmitButton } from "@/components/submit-button";
import { Notice } from "@/components/ui";

export function RunForm() {
  const [state, action] = useActionState<FormState, FormData>(runReconciliation, {});
  return (
    <form action={action} className="mt-4 space-y-3">
      <div>
        <label htmlFor="business_date" className="label">
          Business date (IST)
        </label>
        <input id="business_date" name="business_date" type="date" className="input" />
        <p className="mt-1 text-xs text-zinc-500">Leave empty for today.</p>
      </div>
      <SubmitButton pendingText="Reconciling… (a sleeping bank can take up to a minute)">Run now</SubmitButton>
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {state.message && <Notice tone="success">{state.message}</Notice>}
    </form>
  );
}
