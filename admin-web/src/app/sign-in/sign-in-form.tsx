"use client";

import { useActionState } from "react";

import { signIn, type FormState } from "@/app/actions";
import { SubmitButton } from "@/components/submit-button";
import { Notice } from "@/components/ui";

export function SignInForm({ askForCode }: { askForCode: boolean }) {
  const [state, action] = useActionState<FormState, FormData>(signIn, {});
  return (
    <form action={action} className="space-y-4">
      <div>
        <label htmlFor="email" className="label">
          Email
        </label>
        <input id="email" name="email" type="email" autoComplete="username" required className="input" />
      </div>
      <div>
        <label htmlFor="password" className="label">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          className="input"
        />
      </div>
      {askForCode && (
        <div>
          <label htmlFor="totp_code" className="label">
            Authenticator code
          </label>
          <input
            id="totp_code"
            name="totp_code"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            placeholder="6 digits"
            required
            className="input font-mono tracking-widest"
          />
        </div>
      )}
      {state.error && <Notice tone="error">{state.error}</Notice>}
      <div className="pt-1">
        <SubmitButton pendingText="Signing in…">Sign in</SubmitButton>
      </div>
    </form>
  );
}
