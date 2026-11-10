import * as ExpoCrypto from "expo-crypto";

// @noble/curves needs crypto.getRandomValues. Supply Expo's cryptographically secure one where the runtime has
// none. (Not Crypto.getRandomBytes: Expo documents that it can fall back to Math.random during development.)
const g = globalThis as { crypto?: { getRandomValues?: unknown } };
if (typeof g.crypto?.getRandomValues !== "function") {
  g.crypto = { ...(g.crypto ?? {}), getRandomValues: ExpoCrypto.getRandomValues };
}
