import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";

import { api } from "@/core/api/client";
import type { CodeVerified, OtpRequested } from "@/core/api/types";
import { useSensitiveScreen } from "@/core/security/screen-protection";
import { Body, Button, Field, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

export default function Verify() {
  useSensitiveScreen("verify-code");
  const params = useLocalSearchParams<{ challengeId: string; sentTo: string; kind: string; identifier: string }>();
  const [challengeId, setChallengeId] = useState(params.challengeId);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resendIn, setResendIn] = useState(30);

  useEffect(() => {
    const timer = setInterval(() => setResendIn((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(timer);
  }, []);

  async function verify() {
    setError(null);
    setBusy(true);
    try {
      const verified = await api<CodeVerified>("/v1/auth/otp/verify", {
        method: "POST",
        body: { challenge_id: challengeId, code: code.trim() },
      });
      router.replace({
        pathname: "/pin",
        params: {
          token: verified.enrolment_token,
          step: verified.next_step,
          termsVersion: verified.terms?.version ?? "",
          termsTitle: verified.terms?.title ?? "",
          termsSummary: verified.terms?.summary ?? "",
        },
      });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setError(null);
    setInfo(null);
    try {
      const sent = await api<OtpRequested>("/v1/auth/otp/request", {
        method: "POST",
        body: { kind: params.kind, identifier: params.identifier },
      });
      setChallengeId(sent.challenge_id);
      setResendIn(sent.resend_after_seconds);
      setCode("");
      setInfo("A new code is on its way.");
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  return (
    <Screen>
      <Title>Enter the code</Title>
      <Body muted>We sent a 6-digit code to {params.sentTo}. It expires in 5 minutes.</Body>
      <Field
        label="Code"
        value={code}
        onChangeText={(t) => setCode(t.replace(/\D/g, "").slice(0, 6))}
        keyboardType="number-pad"
        autoComplete="sms-otp"
        textContentType="oneTimeCode"
        placeholder="123456"
        maxLength={6}
      />
      {error && <Message tone="error">{error}</Message>}
      {info && <Message tone="success">{info}</Message>}
      <Button title="Continue" onPress={verify} loading={busy} disabled={code.length !== 6} />
      <Button
        title={resendIn > 0 ? `Send a new code in ${resendIn}s` : "Send a new code"}
        onPress={resend}
        disabled={resendIn > 0}
        variant="secondary"
      />
    </Screen>
  );
}
