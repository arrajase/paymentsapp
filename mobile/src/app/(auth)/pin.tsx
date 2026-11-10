import { router, useLocalSearchParams } from "expo-router";
import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { api } from "@/core/api/client";
import type { Enrolled } from "@/core/api/types";
import { createDeviceKey, HARDWARE_BACKED } from "@/core/security/device-key";
import { useSensitiveScreen } from "@/core/security/screen-protection";
import { useSession } from "@/core/session";
import { Body, Button, Card, colors, Field, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

/** After the code: new customers accept the terms and choose a PIN; others set or enter theirs. */
export default function Pin() {
  useSensitiveScreen("pin");
  const { token, step, termsVersion, termsTitle, termsSummary } = useLocalSearchParams<{
    token: string;
    step: "create_account" | "set_pin" | "enter_pin";
    termsVersion: string;
    termsTitle: string;
    termsSummary: string;
  }>();
  const { completeEnrolment } = useSession();
  const choosing = step !== "enter_pin";
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ready =
    /^\d{4,6}$/.test(pin) && (!choosing || pin === confirm) && (step !== "create_account" || accepted);

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      const publicKey = await createDeviceKey();
      const enrolled = await api<Enrolled>("/v1/auth/devices", {
        method: "POST",
        body: {
          enrolment_token: token,
          public_key: publicKey,
          platform: "android",
          hardware_backed: HARDWARE_BACKED,
          label: "Android emulator",
          pin,
          accepted_terms_version: step === "create_account" ? termsVersion : null,
        },
      });
      await completeEnrolment(enrolled);
      router.replace("/home");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Title>{step === "create_account" ? "Create your account" : step === "set_pin" ? "Set your app PIN" : "Enter your app PIN"}</Title>

      {step === "create_account" && (
        <Card>
          <Text style={styles.termsTitle}>{termsTitle}</Text>
          <Body muted>{termsSummary}</Body>
          <Pressable
            onPress={() => setAccepted(!accepted)}
            style={styles.check}
            accessibilityRole="checkbox"
            accessibilityState={{ checked: accepted }}
          >
            <View style={[styles.box, accepted && styles.boxOn]}>{accepted && <Text style={styles.tick}>✓</Text>}</View>
            <Text style={styles.checkLabel}>I accept the terms and privacy notice (version {termsVersion})</Text>
          </Pressable>
        </Card>
      )}

      <Body muted>
        {choosing
          ? "Choose a 4 to 6 digit PIN. You'll use it to unlock the app and approve payments. Avoid 1234 or 1111."
          : "This signs you in on this phone. Your other phone will be signed out."}
      </Body>
      <Field label="App PIN" value={pin} onChangeText={(t) => setPin(t.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad" secureTextEntry maxLength={6} />
      {choosing && (
        <Field label="Repeat PIN" value={confirm} onChangeText={(t) => setConfirm(t.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad" secureTextEntry maxLength={6} />
      )}
      {choosing && confirm.length > 0 && pin !== confirm && <Message tone="error">The PINs don&apos;t match.</Message>}
      {error && <Message tone="error">{error}</Message>}
      <Button title={step === "create_account" ? "Create account" : "Continue"} onPress={submit} loading={busy} disabled={!ready} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  termsTitle: { fontSize: 16, fontWeight: "600", color: colors.text },
  check: { flexDirection: "row", alignItems: "center", gap: 10 },
  box: { width: 22, height: 22, borderRadius: 4, borderWidth: 1.5, borderColor: colors.border, alignItems: "center", justifyContent: "center" },
  boxOn: { backgroundColor: colors.primary, borderColor: colors.primary },
  tick: { color: colors.primaryText, fontWeight: "700" },
  checkLabel: { flex: 1, color: colors.text },
});
