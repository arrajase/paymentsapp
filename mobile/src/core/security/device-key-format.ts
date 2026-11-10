// Encodings shared by the device key code and its compatibility check (no React Native imports here).
import { p256 } from "@noble/curves/nist.js";
import { bytesToHex, hexToBytes } from "@noble/curves/utils.js";

// DER prefix of a SubjectPublicKeyInfo for an uncompressed P-256 public key; the 65-byte point follows it.
const SPKI_P256_PREFIX = hexToBytes("3059301306072a8648ce3d020106082a8648ce3d030107034200");

export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

/** The public key as the backend expects it: base64 DER SubjectPublicKeyInfo. */
export function publicKeySpkiBase64(secretKeyHex: string): string {
  const point = p256.getPublicKey(hexToBytes(secretKeyHex), false); // uncompressed: 0x04 || x || y
  const spki = new Uint8Array(SPKI_P256_PREFIX.length + point.length);
  spki.set(SPKI_P256_PREFIX);
  spki.set(point, SPKI_P256_PREFIX.length);
  return toBase64(spki);
}

/** ECDSA P-256 over SHA-256 of the text, DER-encoded and base64, as the backend verifies it. */
export function signBase64(secretKeyHex: string, text: string): string {
  const compact = p256.sign(new TextEncoder().encode(text), hexToBytes(secretKeyHex)); // prehash: SHA-256
  return toBase64(p256.Signature.fromBytes(compact, "compact").toBytes("der"));
}

/** A new secret key from caller-supplied secure random bytes (at least 48 for an unbiased scalar). */
export function newSecretKeyHex(seed: Uint8Array): string {
  return bytesToHex(p256.utils.randomSecretKey(seed));
}
