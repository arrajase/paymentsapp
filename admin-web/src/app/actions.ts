"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";

import { ApiError, backend, signInRequest } from "@/lib/api";
import { SESSION_COOKIE } from "@/lib/session-cookie";
import type { ChangeRequest } from "@/lib/types";

// Every action passes the admin's own session to the backend, which checks the role again. Nothing here trusts
// that the form was shown only to someone allowed to submit it.

export type FormState = { error?: string; message?: string };

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function failure(error: unknown): FormState {
  if (error instanceof ApiError) return { error: error.message };
  throw error; // includes Next.js redirects, which must propagate
}

export async function signIn(_: FormState, form: FormData): Promise<FormState> {
  const email = text(form, "email");
  const password = typeof form.get("password") === "string" ? (form.get("password") as string) : "";
  const totp_code = text(form, "totp_code").replace(/\s/g, "");
  if (!email || !password) return { error: "Enter your email and password." };
  // The backend decides whether a code is required; only check the format of one that was entered.
  if (totp_code && !/^\d{6}$/.test(totp_code)) return { error: "The authenticator code is 6 digits." };
  try {
    const result = await signInRequest({ email, password, totp_code: totp_code || undefined });
    (await cookies()).set(SESSION_COOKIE, result.token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      expires: new Date(result.expires_at),
    });
  } catch (error) {
    return failure(error);
  }
  redirect("/plugins");
}

export async function signOut(): Promise<void> {
  try {
    await backend<void>("/admin/v1/auth/sign-out", { method: "POST" });
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
  }
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/sign-in");
}

export async function proposeChange(pluginKey: string, _: FormState, form: FormData): Promise<FormState> {
  const config: Record<string, string> = {};
  const secrets: Record<string, string> = {};
  for (const [name, value] of form.entries()) {
    if (typeof value !== "string") continue;
    if (name.startsWith("config.")) config[name.slice(7)] = value.trim();
    if (name.startsWith("secret.") && value.trim()) secrets[name.slice(7)] = value.trim();
  }
  const body = {
    reason: text(form, "reason"),
    enabled: form.get("enabled") === "on",
    mode: text(form, "mode") || undefined,
    config,
    secrets,
  };
  try {
    await backend<ChangeRequest>(`/admin/v1/plugins/${encodeURIComponent(pluginKey)}/change-requests`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/plugins");
  revalidatePath("/approvals");
  redirect("/approvals");
}

export async function decide(
  requestId: string,
  decision: "approve" | "reject",
  _: FormState,
  form: FormData,
): Promise<FormState> {
  const note = text(form, "note");
  if (decision === "reject" && note.length < 10) {
    return { error: "Give a reason of at least 10 characters for rejecting." };
  }
  try {
    await backend<ChangeRequest>(`/admin/v1/change-requests/${encodeURIComponent(requestId)}/${decision}`, {
      method: "POST",
      body: JSON.stringify({ note: note || null }),
    });
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/approvals");
  revalidatePath("/plugins");
  return { message: decision === "approve" ? "Approved and applied." : "Rejected." };
}

export async function cancelRequest(requestId: string, _: FormState): Promise<FormState> {
  try {
    await backend<ChangeRequest>(`/admin/v1/change-requests/${encodeURIComponent(requestId)}/cancel`, {
      method: "POST",
    });
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/approvals");
  revalidatePath("/plugins");
  return { message: "Withdrawn." };
}
