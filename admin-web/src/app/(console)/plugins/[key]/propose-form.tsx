"use client";

import { useActionState } from "react";

import { proposeChange, type FormState } from "@/app/actions";
import { SubmitButton } from "@/components/submit-button";
import { Notice } from "@/components/ui";
import type { Plugin } from "@/lib/types";

export function ProposeForm({ plugin }: { plugin: Plugin }) {
  const [state, action] = useActionState<FormState, FormData>(proposeChange.bind(null, plugin.key), {});
  return (
    <form action={action} className="mt-4 space-y-4">
      <label className="flex items-center gap-2 text-sm font-medium text-zinc-700">
        <input type="checkbox" name="enabled" defaultChecked={plugin.enabled} className="h-4 w-4" />
        Enabled
      </label>

      <div>
        <label htmlFor="mode" className="label">
          Mode
        </label>
        <select id="mode" name="mode" defaultValue={plugin.mode} className="input">
          {plugin.allowed_modes.map((mode) => (
            <option key={mode} value={mode}>
              {mode}
            </option>
          ))}
        </select>
      </div>

      {plugin.config.map((field) => (
        <div key={field.name}>
          <label htmlFor={`config.${field.name}`} className="label">
            {field.label}
          </label>
          <input
            id={`config.${field.name}`}
            name={`config.${field.name}`}
            defaultValue={field.value ?? ""}
            className="input font-mono"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
      ))}

      {plugin.secrets.map((secret) => (
        <div key={secret.name}>
          <label htmlFor={`secret.${secret.name}`} className="label">
            {secret.label}
          </label>
          <input
            id={`secret.${secret.name}`}
            name={`secret.${secret.name}`}
            type="password"
            placeholder={secret.preview ? `Leave blank to keep ${secret.preview}` : "Not set"}
            className="input font-mono"
            autoComplete="new-password"
            spellCheck={false}
          />
        </div>
      ))}

      <div>
        <label htmlFor="reason" className="label">
          Reason for the change
        </label>
        <textarea
          id="reason"
          name="reason"
          required
          minLength={10}
          maxLength={500}
          rows={3}
          placeholder="Why is this change needed? The approver sees this."
          className="input"
        />
      </div>

      {state.error && <Notice tone="error">{state.error}</Notice>}
      <div className="flex items-center gap-3">
        <SubmitButton pendingText="Submitting…">Submit for approval</SubmitButton>
        <span className="muted">Nothing changes until a security approver approves it.</span>
      </div>
    </form>
  );
}
