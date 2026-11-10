import * as LocalAuthentication from "expo-local-authentication";
import { router } from "expo-router";
import { useEffect, useState } from "react";

import { useSensitiveScreen } from "@/core/security/screen-protection";
import { useSession } from "@/core/session";
import { storage } from "@/core/storage";
import { Body, Button, Field, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

/** Returning on a registered phone: no code needed, just the app PIN or a fingerprint. */
export default function Unlock() {
  useSensitiveScreen("unlock");
  const { unlock, forgetDevice } = useSession();
  const [pin, setPin] = useState("");
  const [canUseFingerprint, setCanUseFingerprint] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      const [enabled, hardware, enrolled] = await Promise.all([
        storage.getBiometricUnlock(),
        LocalAuthentication.hasHardwareAsync(),
        LocalAuthentication.isEnrolledAsync(),
      ]);
      setCanUseFingerprint(enabled && hardware && enrolled);
    })();
  }, []);

  async function run(method: { pin: string } | { biometric: true }) {
    setError(null);
    setBusy(true);
    try {
      await unlock(method);
      router.replace("/home");
    } catch (e) {
      setError(errorMessage(e));
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Title>Unlock paymentsapp</Title>
      <Body muted>Enter your app PIN.</Body>
      <Field label="App PIN" value={pin} onChangeText={(t) => setPin(t.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad" secureTextEntry maxLength={6} />
      {error && <Message tone="error">{error}</Message>}
      <Button title="Unlock" onPress={() => run({ pin })} loading={busy} disabled={pin.length < 4} />
      {canUseFingerprint && <Button title="Use fingerprint" onPress={() => run({ biometric: true })} variant="secondary" />}
      <Button
        title="Sign in with a code instead"
        variant="secondary"
        onPress={async () => {
          await forgetDevice();
          router.replace("/sign-in");
        }}
      />
    </Screen>
  );
}
