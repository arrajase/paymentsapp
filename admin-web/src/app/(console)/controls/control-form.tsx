"use client";

import { useActionState } from "react";

import { proposeControlChange, type FormState } from "@/app/actions";
import { SubmitButton } from "@/components/submit-button";
import { Notice } from "@/components/ui";
import { showValue } from "@/lib/format";
import type { Control } from "@/lib/types";

export function ControlForm({ control }: { control: Control }) {
  const [state, action] = useActionState<FormState, FormData>(
    proposeControlChange.bind(null, control.key, control.kind),
    {},
  );
  const id = `control-${control.key}`;
  return (
    <form action={action} className="space-y-2">
      <div className="flex gap-2">
        {control.kind === "number" ? (
          <input
            id={`${id}-value`}
            name="value"
            type="number"
            aria-label="New value"
            defaultValue={String(control.value)}
            min={control.bounds?.[0]}
            max={control.bounds?.[1]}
            required
            className="input w-28"
          />
        ) : (
          <select
            id={`${id}-value`}
            name="value"
            aria-label="New value"
            defaultValue={String(control.value)}
            className="input w-36"
          >
            {control.allowed.map((option) => (
              <option key={String(option)} value={String(option)}>
                {showValue(option)}
              </option>
            ))}
          </select>
        )}
        <input
          id={`${id}-reason`}
          name="reason"
          aria-label="Reason"
          placeholder="Reason (at least 10 characters)"
          required
          minLength={10}
          maxLength={500}
          className="input"
        />
      </div>
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {state.message && <Notice tone="success">{state.message}</Notice>}
      <SubmitButton variant="secondary" pendingText="Submitting…">
        Propose change
      </SubmitButton>
    </form>
  );
}
