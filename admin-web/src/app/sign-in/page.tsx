import { Notice } from "@/components/ui";
import { getSignInOptions } from "@/lib/api";

import { SignInForm } from "./sign-in-form";

export default async function SignInPage({ searchParams }: PageProps<"/sign-in">) {
  const [{ expired }, options] = await Promise.all([searchParams, getSignInOptions()]);
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <p className="text-xs font-semibold uppercase tracking-widest text-indigo-600">paymentsapp</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Admin sign-in</h1>
          <p className="mt-1 muted">For internal staff only.</p>
          {!options.totp_required && (
            <p className="mt-2 text-xs text-amber-700">Demo mode: authenticator code switched off (development only).</p>
          )}
        </div>
        {expired && (
          <div className="mb-4">
            <Notice tone="warning">Your session ended. Sign in again.</Notice>
          </div>
        )}
        <div className="card">
          <SignInForm askForCode={options.totp_required} />
        </div>
      </div>
    </main>
  );
}
