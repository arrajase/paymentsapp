import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SESSION_COOKIE } from "@/lib/session-cookie";
import type { Admin, AuditEntry, ChangeRequest, Plugin, RequestStatus, SystemInfo } from "@/lib/types";

const BASE_URL = process.env.ADMIN_API_BASE_URL ?? "http://localhost:8001";

/** An error the backend reported in its standard shape: {"error": {"code", "message"}}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function sessionToken(): Promise<string | undefined> {
  return (await cookies()).get(SESSION_COOKIE)?.value;
}

/**
 * Call the backend's admin API with the signed-in admin's token. Runs only on the server: the token lives in an
 * HttpOnly cookie and is never visible to browser code. An expired or revoked session sends the admin to sign-in.
 */
export async function backend<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await sessionToken();
  if (!token) redirect("/sign-in");

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        Authorization: `Bearer ${token}`,
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError(503, "BACKEND_UNREACHABLE", "The backend is not reachable. Is it running on port 8001?");
  }

  if (response.status === 401) redirect("/sign-in?expired=1");
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.code ?? "HTTP_ERROR",
      body?.error?.message ?? `Request failed (${response.status})`,
    );
  }
  return body as T;
}

/** Public: whether the sign-in page should ask for an authenticator code. Defaults to asking if unreachable. */
export async function getSignInOptions(): Promise<{ totp_required: boolean }> {
  try {
    const response = await fetch(`${BASE_URL}/admin/v1/auth/options`, { cache: "no-store" });
    if (response.ok) return (await response.json()) as { totp_required: boolean };
  } catch {
    // fall through
  }
  return { totp_required: true };
}

/** Sign-in is the one call made without a token. */
export async function signInRequest(body: { email: string; password: string; totp_code?: string }) {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/admin/v1/auth/sign-in`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ login_id: body.email, password: body.password, totp_code: body.totp_code || null }),
    });
  } catch {
    throw new ApiError(503, "BACKEND_UNREACHABLE", "The backend is not reachable. Is it running on port 8001?");
  }
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, json?.error?.code ?? "HTTP_ERROR", json?.error?.message ?? "Sign-in failed");
  }
  return json as { token: string; expires_at: string; admin: Admin };
}

// ---- Typed helpers -------------------------------------------------------------------------------------------

export const getMe = () => backend<Admin>("/admin/v1/me");
export const getSystem = () => backend<SystemInfo>("/admin/v1/system");
export const getPlugins = () => backend<Plugin[]>("/admin/v1/plugins");
export const getAudit = (limit = 100) => backend<AuditEntry[]>(`/admin/v1/audit?limit=${limit}`);
export const getChangeRequests = (status?: RequestStatus) =>
  backend<ChangeRequest[]>(`/admin/v1/change-requests${status ? `?status=${status}` : ""}`);
