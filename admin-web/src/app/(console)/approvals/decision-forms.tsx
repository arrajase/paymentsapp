"use client";

import { useActionState } from "react";

import { cancelRequest, decide, type FormState } from "@/app/actions";
import { SubmitButton } from "@/components/submit-button";
import { Notice } from "@/components/ui";

export function DecisionForm({ requestId }: { requestId: string }) {
  const [approveState, approve] = useActionState<FormState, FormData>(decide.bind(null, requestId, "approve"), {});
  const [rejectState, reject] = useActionState<FormState, FormData>(decide.bind(null, requestId, "reject"), {});
  const state = rejectState.error || rejectState.message ? rejectState : approveState;
  return (
    <form className="mt-4 space-y-3 border-t border-zinc-100 pt-4">
      <div>
        <label htmlFor={`note-${requestId}`} className="label">
          Note <span className="font-normal text-zinc-500">(required to reject)</span>
        </label>
        <input id={`note-${requestId}`} name="note" maxLength={500} className="input" />
      </div>
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {state.message && <Notice tone="success">{state.message}</Notice>}
      <div className="flex gap-3">
        <button formAction={approve} type="submit" className="btn-primary">
          Approve and apply
        </button>
        <button formAction={reject} type="submit" className="btn-danger">
          Reject
        </button>
      </div>
    </form>
  );
}

export function CancelForm({ requestId }: { requestId: string }) {
  const [state, action] = useActionState<FormState, FormData>(cancelRequest.bind(null, requestId), {});
  return (
    <form action={action} className="mt-4 border-t border-zinc-100 pt-4">
      {state.error && (
        <div className="mb-3">
          <Notice tone="error">{state.error}</Notice>
        </div>
      )}
      <SubmitButton variant="secondary" pendingText="Withdrawing…">
        Withdraw my request
      </SubmitButton>
    </form>
  );
}
