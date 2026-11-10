import { router, Stack, useLocalSearchParams } from "expo-router";
import { useState } from "react";

import { api } from "@/core/api/client";
import type { IdentifierKind, OtpRequested } from "@/core/api/types";
import { useSensitiveScreen } from "@/core/security/screen-protection";
import { Body, Button, Field, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

export default function AddIdentifier() {
  useSensitiveScreen("add-identifier");
  const { kind } = useLocalSearchParams<{ kind: IdentifierKind }>();
  const [value, setValue] = useState("");
  const [sent, setSent] = useState<OtpRequested | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const label = kind === "mobile" ? "Mobile number" : "Email";

  async function sendCode() {
    setError(null);
    setBusy(true);
    try {
      setSent(await api<OtpRequested>("/v1/me/identifiers/otp", { method: "POST", body: { kind, identifier: value }, auth: true }));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setError(null);
    setBusy(true);
    try {
      await api("/v1/me/identifiers/verify", { method: "POST", body: { challenge_id: sent?.challenge_id, code }, auth: true });
      router.back();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: `Add ${kind === "mobile" ? "mobile" : "email"}` }} />
      <Title>Add your {label.toLowerCase()}</Title>
      {!sent ? (
        <>
          <Field
            label={label}
            value={value}
            onChangeText={setValue}
            keyboardType={kind === "mobile" ? "phone-pad" : "email-address"}
            autoCapitalize="none"
            placeholder={kind === "mobile" ? "+91 90000 00002" : "you@example.com"}
          />
          {error && <Message tone="error">{error}</Message>}
          <Button title="Send code" onPress={sendCode} loading={busy} disabled={value.trim().length < 5} />
        </>
      ) : (
        <>
          <Body muted>Enter the code we sent to {sent.sent_to}.</Body>
          <Field label="Code" value={code} onChangeText={(t) => setCode(t.replace(/\D/g, "").slice(0, 6))} keyboardType="number-pad" autoComplete="sms-otp" maxLength={6} />
          {error && <Message tone="error">{error}</Message>}
          <Button title="Verify" onPress={verify} loading={busy} disabled={code.length !== 6} />
        </>
      )}
    </Screen>
  );
}
