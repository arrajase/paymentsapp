import * as ExpoCrypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { newSecretKeyHex, publicKeySpkiBase64, signBase64 } from "./device-key-format";

// The device's ECDSA P-256 key (plan M1). Here it's a software key kept in SecureStore, which Android encrypts with
// the Keystore. That's what the emulator can do, and why the hardware_device_key control is "off" in development.
// A development build will swap in a hardware-backed, non-exportable Keystore key behind these same functions.
const SECRET_KEY = "device_key_secret";

export const HARDWARE_BACKED = false;

export async function hasDeviceKey(): Promise<boolean> {
  return (await SecureStore.getItemAsync(SECRET_KEY)) !== null;
}

/** Create a fresh key pair, replacing any old one, and return its public key for registration. */
export async function createDeviceKey(): Promise<string> {
  const seed = ExpoCrypto.getRandomValues(new Uint8Array(48));
  const secret = newSecretKeyHex(seed);
  await SecureStore.setItemAsync(SECRET_KEY, secret);
  return publicKeySpkiBase64(secret);
}

export async function signWithDeviceKey(text: string): Promise<string> {
  const secret = await SecureStore.getItemAsync(SECRET_KEY);
  if (!secret) throw new Error("This device has no key. Sign in with a code.");
  return signBase64(secret, text);
}

export async function deleteDeviceKey(): Promise<void> {
  await SecureStore.deleteItemAsync(SECRET_KEY);
}
