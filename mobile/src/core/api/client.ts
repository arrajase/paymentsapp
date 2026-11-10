import { API_URL } from "@/core/config";
import { storage } from "@/core/storage";

import type { Tokens } from "./types";

/** An error in the backend's standard shape: {"error": {"code", "message"}}. The message is safe to show. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

// The access token lives only in memory; on restart the app unlocks again (PIN or fingerprint).
let accessToken: string | null = null;
let onSignedOut: (() => void) | null = null;

export const tokens = {
  set(pair: Tokens) {
    accessToken = pair.access_token;
    return storage.setRefreshToken(pair.refresh_token);
  },
  clear() {
    accessToken = null;
    return storage.clearRefreshToken();
  },
  hasAccess: () => accessToken !== null,
  /** Called when the session can't be renewed, so the app can send the customer to the unlock screen. */
  onSignedOut(callback: () => void) {
    onSignedOut = callback;
  },
};

type Options = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  auth?: boolean;
  headers?: Record<string, string>;
};

async function send<T>(path: string, { method = "GET", body, auth = false, headers: extra }: Options): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...extra };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "NETWORK", `Can't reach the server at ${API_URL}. Is the backend running?`);
  }
  if (response.status === 204) return undefined as T;
  const json = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      json?.error?.code ?? "HTTP_ERROR",
      json?.error?.message ?? `Something went wrong (${response.status})`,
    );
  }
  return json as T;
}

let refreshing: Promise<boolean> | null = null;

/** Swap the refresh token for a new pair. Only one refresh runs at a time; a reused token ends the session. */
async function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const [refreshToken, deviceId] = await Promise.all([storage.getRefreshToken(), storage.getDeviceId()]);
    if (!refreshToken || !deviceId) return false;
    try {
      const pair = await send<Tokens>("/v1/auth/token/refresh", {
        method: "POST",
        body: { refresh_token: refreshToken, device_id: deviceId },
      });
      await tokens.set(pair);
      return true;
    } catch {
      await tokens.clear();
      return false;
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

/** Call the backend. With auth, an expired access token is refreshed once before giving up. */
export async function api<T>(path: string, options: Options = {}): Promise<T> {
  try {
    return await send<T>(path, options);
  } catch (error) {
    // Only an invalid session is worth a refresh. Other 401s (a wrong PIN, a used challenge) are answers to show,
    // and repeating the request would only turn them into a misleading "expired".
    if (!(options.auth && error instanceof ApiError && error.status === 401 && error.code === "UNAUTHORIZED")) throw error;
    if (await refreshTokens()) return send<T>(path, options);
    onSignedOut?.();
    throw error;
  }
}
