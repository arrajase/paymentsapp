import { router, Stack } from "expo-router";
import { useEffect, useState } from "react";
import { Linking, Text } from "react-native";

import { api, ApiError } from "@/core/api/client";
import type { UpiCandidate, UpiDiscover, UpiLinkCreated } from "@/core/api/types";
import { Body, Button, Card, colors, Field, Loading, Message, Row, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";
import { isCancelled, useStepUp } from "@/ui/step-up-prompt";

/**
 * Link a UPI ID (plan M3). The backend first asks the bank which UPI ID is linked to the customer's verified mobile;
 * the customer can also type another. Linking needs the app PIN, then approval with the UPI PIN on MockBank.
 */
export default function LinkUpi() {
  const [discovered, setDiscovered] = useState<UpiCandidate | null | undefined>(undefined);
  const [typed, setTyped] = useState("");
  const [checked, setChecked] = useState<UpiCandidate | null>(null);
  const [created, setCreated] = useState<UpiLinkCreated | null>(null);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const { confirm, prompt } = useStepUp();

  function fail(e: unknown) {
    setError({ code: e instanceof ApiError ? e.code : "ERROR", message: errorMessage(e) });
  }

  useEffect(() => {
    api<UpiDiscover>("/v1/upi/discover", { auth: true }).then(
      (found) => setDiscovered(found.candidate),
      (e) => {
        setDiscovered(null);
        fail(e);
      },
    );
  }, []);

  async function check() {
    setError(null);
    setChecked(null);
    setBusy(true);
    try {
      setChecked(await api<UpiCandidate>("/v1/upi/validate", { method: "POST", body: { upi_id: typed }, auth: true }));
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function link(upiId: string) {
    setError(null);
    try {
      const token = await confirm("upi_link", "Link this UPI ID");
      setBusy(true);
      setCreated(
        await api<UpiLinkCreated>("/v1/upi/links", {
          method: "POST",
          body: { upi_id: upiId },
          auth: true,
          headers: { "X-Step-Up-Token": token },
        }),
      );
    } catch (e) {
      if (!isCancelled(e)) fail(e);
    } finally {
      setBusy(false);
    }
  }

  if (discovered === undefined) return <Loading />;

  if (created) {
    return (
      <Screen>
        <Stack.Screen options={{ title: "Approve at your bank" }} />
        <Title>Approve on MockBank</Title>
        <Body>
          To prove {created.upi_id} is yours, approve the request at your bank. Sign in to MockBank as the account
          holder, open App Permissions, and allow the paymentsapp request with your UPI PIN.
        </Body>
        <Body muted>{"Your UPI PIN is entered only on the bank's website, never in this app."}</Body>
        {created.approve_at && <Button title="Open MockBank" onPress={() => Linking.openURL(created.approve_at!)} />}
        <Button title="I've approved it" variant="secondary" onPress={() => router.replace("/upi")} />
      </Screen>
    );
  }

  const blocked = error && ["KYC_REQUIRED", "MOBILE_REQUIRED", "FEATURE_OFF", "DEVICE_COOLING"].includes(error.code);

  return (
    <Screen>
      <Stack.Screen options={{ title: "Link a UPI ID" }} />
      <Title>Link your bank account</Title>
      {error && <Message tone="error">{error.message}</Message>}
      {error?.code === "KYC_REQUIRED" && <Button title="Complete minimum KYC" onPress={() => router.replace("/kyc")} />}

      {!blocked && (
        <>
          {discovered ? (
            <>
              <Body muted>We found this UPI ID linked to your mobile number at your bank.</Body>
              <CandidateCard candidate={discovered} />
              {discovered.can_link && <Button title="Link this UPI ID" onPress={() => link(discovered.upi_id)} loading={busy} />}
            </>
          ) : (
            <Body muted>{"Your bank hasn't linked a UPI ID to your mobile number. You can enter one instead."}</Body>
          )}

          <Text style={{ fontWeight: "600", color: colors.text, marginTop: 8 }}>
            {discovered ? "Or use a different UPI ID" : "Enter your UPI ID"}
          </Text>
          <Field
            label="UPI ID"
            value={typed}
            onChangeText={(t) => {
              setTyped(t);
              setChecked(null);
            }}
            autoCapitalize="none"
            autoCorrect={false}
            placeholder="name@okmockbank"
          />
          {!checked && <Button title="Check" variant="secondary" onPress={check} loading={busy} disabled={!typed.includes("@")} />}
          {checked && (
            <>
              <CandidateCard candidate={checked} />
              {checked.can_link && <Button title="Link this UPI ID" onPress={() => link(checked.upi_id)} loading={busy} />}
            </>
          )}
        </>
      )}
      {prompt}
    </Screen>
  );
}

function CandidateCard({ candidate }: { candidate: UpiCandidate }) {
  return (
    <Card>
      <Row label="UPI ID" value={candidate.upi_id} />
      <Row label="Account holder" value={candidate.holder_name_masked} />
      <Row label="Name matches your KYC" value={candidate.name_matches ? "Yes" : "No"} />
      {candidate.problem && <Message tone="error">{candidate.problem}</Message>}
    </Card>
  );
}
