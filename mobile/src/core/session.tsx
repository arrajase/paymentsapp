import * as LocalAuthentication from "expo-local-authentication";
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, tokens } from "@/core/api/client";
import type { AppConfig, Challenge, Enrolled, Tokens } from "@/core/api/types";
import { deleteDeviceKey, hasDeviceKey, signWithDeviceKey } from "@/core/security/device-key";
import { storage } from "@/core/storage";

// "new": this phone has never signed in (or was signed out by another device).
// "locked": registered, but needs the app PIN or a fingerprint to unlock.
// "signed_in": has a valid session.
export type Phase = "loading" | "new" | "locked" | "signed_in";

type Session = {
  phase: Phase;
  appConfig: AppConfig | null;
  completeEnrolment(enrolled: Enrolled): Promise<void>;
  unlock(method: { pin: string } | { biometric: true }): Promise<void>;
  signOut(): Promise<void>;
  forgetDevice(): Promise<void>;
};

const SessionContext = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [appConfig, setAppConfig] = useState<AppConfig | null>(null);

  const forgetDevice = useCallback(async () => {
    await Promise.all([storage.forgetDevice(), deleteDeviceKey(), tokens.clear()]);
    setPhase("new");
  }, []);

  useEffect(() => {
    tokens.onSignedOut(() => setPhase("locked"));
    (async () => {
      // The app follows the server's controls, e.g. screenshot protection. The server enforces them regardless.
      api<AppConfig>("/v1/app-config").then(setAppConfig, () => setAppConfig(null));
      const [deviceId, keyExists] = await Promise.all([storage.getDeviceId(), hasDeviceKey()]);
      setPhase(deviceId && keyExists ? "locked" : "new");
    })();
  }, []);

  const completeEnrolment = useCallback(async (enrolled: Enrolled) => {
    await storage.setDeviceId(enrolled.device_id);
    await tokens.set(enrolled.tokens);
    setPhase("signed_in");
  }, []);

  const unlock = useCallback(
    async (method: { pin: string } | { biometric: true }) => {
      const deviceId = await storage.getDeviceId();
      if (!deviceId) return forgetDevice();
      if ("biometric" in method) {
        const result = await LocalAuthentication.authenticateAsync({ promptMessage: "Unlock paymentsapp" });
        if (!result.success) throw new ApiError(0, "BIOMETRIC_CANCELLED", "Fingerprint not confirmed.");
      }
      try {
        const challenge = await api<Challenge>("/v1/auth/challenge", { method: "POST", body: { device_id: deviceId } });
        const signature = await signWithDeviceKey(challenge.sign);
        const pair = await api<Tokens>("/v1/auth/sign-in", {
          method: "POST",
          body: {
            device_id: deviceId,
            challenge_id: challenge.challenge_id,
            signature,
            ...("pin" in method ? { pin: method.pin } : { biometric: true }),
          },
        });
        await tokens.set(pair);
        setPhase("signed_in");
      } catch (error) {
        // The backend no longer knows this device (for example, another phone signed in): start over with a code.
        if (error instanceof ApiError && error.code === "DEVICE_NOT_RECOGNISED") await forgetDevice();
        throw error;
      }
    },
    [forgetDevice],
  );

  const signOut = useCallback(async () => {
    try {
      await api<void>("/v1/auth/sign-out", { method: "POST", auth: true });
    } finally {
      await tokens.clear();
      setPhase("locked");
    }
  }, []);

  const value = useMemo(
    () => ({ phase, appConfig, completeEnrolment, unlock, signOut, forgetDevice }),
    [phase, appConfig, completeEnrolment, unlock, signOut, forgetDevice],
  );
  return <SessionContext value={value}>{children}</SessionContext>;
}

export function useSession(): Session {
  const session = use(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
