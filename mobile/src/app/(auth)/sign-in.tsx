import { router } from "expo-router";
import { useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import { api } from "@/core/api/client";
import type { IdentifierKind, OtpRequested } from "@/core/api/types";
import { Body, Button, colors, Field, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

export default function SignIn() {
  const [kind, setKind] = useState<IdentifierKind>("mobile");
  const [identifier, setIdentifier] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function sendCode() {
    setError(null);
    setBusy(true);
    try {
      const sent = await api<OtpRequested>("/v1/auth/otp/request", { method: "POST", body: { kind, identifier } });
      router.push({ pathname: "/verify", params: { challengeId: sent.challenge_id, sentTo: sent.sent_to, kind, identifier } });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Title>Welcome to paymentsapp</Title>
      <Body muted>Sign in or create an account. We&apos;ll send you a one-time code.</Body>

      <View style={styles.tabs}>
        {(["mobile", "email"] as const).map((option) => (
          <Text
            key={option}
            onPress={() => setKind(option)}
            style={[styles.tab, kind === option && styles.tabActive]}
            accessibilityRole="button"
            accessibilityState={{ selected: kind === option }}
          >
            {option === "mobile" ? "Mobile number" : "Email"}
          </Text>
        ))}
      </View>

      {kind === "mobile" ? (
        <Field
          label="Mobile number"
          value={identifier}
          onChangeText={setIdentifier}
          placeholder="+91 90000 00001"
          keyboardType="phone-pad"
          autoComplete="tel"
        />
      ) : (
        <Field
          label="Email"
          value={identifier}
          onChangeText={setIdentifier}
          placeholder="you@example.com"
          keyboardType="email-address"
          autoCapitalize="none"
          autoComplete="email"
        />
      )}
      {error && <Message tone="error">{error}</Message>}
      <Button title="Send code" onPress={sendCode} loading={busy} disabled={identifier.trim().length < 5} />
      <Body muted>For testing, use numbers from +91 90000 00001 to +91 90000 00099. Codes appear as an SMS in the emulator and on the admin website&apos;s Dev inbox.</Body>
    </Screen>
  );
}

const styles = StyleSheet.create({
  tabs: { flexDirection: "row", gap: 8 },
  tab: {
    flex: 1,
    textAlign: "center",
    paddingVertical: 10,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    color: colors.text,
    overflow: "hidden",
  },
  tabActive: { backgroundColor: colors.primary, borderColor: colors.primary, color: colors.primaryText, fontWeight: "600" },
});
