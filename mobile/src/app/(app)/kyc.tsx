import { router, Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";

import { api } from "@/core/api/client";
import type { Kyc } from "@/core/api/types";
import { useSensitiveScreen } from "@/core/security/screen-protection";
import { Body, Button, Field, Loading, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

export default function KycScreen() {
  useSensitiveScreen("kyc");
  const [kyc, setKyc] = useState<Kyc | null>(null);
  const [name, setName] = useState("");
  const [dob, setDob] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useFocusEffect(
    useCallback(() => {
      api<Kyc>("/v1/kyc", { auth: true }).then(setKyc, (e) => setError(errorMessage(e)));
    }, []),
  );

  async function submit(path: "/v1/kyc/minimum" | "/v1/kyc/full", body?: unknown) {
    setError(null);
    setBusy(true);
    try {
      setKyc(await api<Kyc>(path, { method: "POST", body, auth: true }));
    } catch (e) {
      const message = errorMessage(e);
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  if (!kyc && !error) return <Loading />;
  return (
    <Screen>
      <Stack.Screen options={{ title: "KYC" }} />
      {kyc?.tier === "T0" && (
        <>
          <Title>Minimum KYC</Title>
          <Body muted>Your name as it appears on your ID, and your date of birth. You must be 18 or older.</Body>
          <Field label="Full name" value={name} onChangeText={setName} autoCapitalize="words" autoComplete="name" />
          <Field label="Date of birth (YYYY-MM-DD)" value={dob} onChangeText={setDob} placeholder="1995-06-15" keyboardType="numbers-and-punctuation" />
          {error && <Message tone="error">{error}</Message>}
          {error?.includes("mobile") && (
            <Button title="Add a mobile number" variant="secondary" onPress={() => router.push({ pathname: "/add-identifier", params: { kind: "mobile" } })} />
          )}
          <Button
            title="Submit"
            onPress={() => submit("/v1/kyc/minimum", { full_name: name, date_of_birth: dob })}
            loading={busy}
            disabled={name.trim().length < 2 || !/^\d{4}-\d{2}-\d{2}$/.test(dob)}
          />
        </>
      )}
      {kyc?.tier === "T1" && (
        <>
          <Title>Full KYC</Title>
          <Body muted>
            Full KYC unlocks wallet transfers and withdrawals. In this test build the mock KYC provider approves it at
            once; real document checks (PAN, Aadhaar) come with a KYC partner.
          </Body>
          {error && <Message tone="error">{error}</Message>}
          <Button title="Verify with the mock KYC provider" onPress={() => submit("/v1/kyc/full")} loading={busy} />
        </>
      )}
      {kyc?.tier === "T2" && (
        <>
          <Title>Full KYC complete</Title>
          <Message tone="success">Your identity is fully verified.</Message>
          <Button title="Back" onPress={() => router.back()} />
        </>
      )}
    </Screen>
  );
}
