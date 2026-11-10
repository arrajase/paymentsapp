import * as LocalAuthentication from "expo-local-authentication";

import { api, ApiError } from "@/core/api/client";
import type { Challenge, StepUp, StepUpAction } from "@/core/api/types";

import { signWithDeviceKey } from "./device-key";

export type StepUpMethod = { pin: string } | { biometric: true };

/**
 * Confirm a sensitive action (plan M1): the device key signs a challenge naming the action, together with the app
 * PIN or a fingerprint. Returns a single-use token for that action, sent as the X-Step-Up-Token header.
 */
export async function stepUp(action: StepUpAction, method: StepUpMethod): Promise<string> {
  if ("biometric" in method) {
    const result = await LocalAuthentication.authenticateAsync({ promptMessage: "Confirm it's you" });
    if (!result.success) throw new ApiError(0, "BIOMETRIC_CANCELLED", "Fingerprint not confirmed.");
  }
  const challenge = await api<Challenge>("/v1/auth/step-up/challenge", { method: "POST", body: { action }, auth: true });
  const signature = await signWithDeviceKey(challenge.sign);
  const done = await api<StepUp>("/v1/auth/step-up", {
    method: "POST",
    body: { challenge_id: challenge.challenge_id, signature, ...("pin" in method ? { pin: method.pin } : { biometric: true }) },
    auth: true,
  });
  return done.step_up_token;
}
