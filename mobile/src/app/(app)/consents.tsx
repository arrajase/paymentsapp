import { Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Switch, Text, View } from "react-native";

import { api } from "@/core/api/client";
import type { Consent } from "@/core/api/types";
import { Body, Card, colors, Loading, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";

/** Each purpose is a separate switch; withdrawing is as easy as giving (plan M9). */
export default function Consents() {
  const [consents, setConsents] = useState<Consent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Consent[]>("/v1/consents", { auth: true }).then(setConsents, (e) => setError(errorMessage(e)));
  }, []);
  useFocusEffect(load);

  async function toggle(consent: Consent, on: boolean) {
    setError(null);
    try {
      const path = `/v1/consents/${consent.notice.purpose}`;
      if (on) await api<void>(path, { method: "POST", body: { notice_version: consent.notice.version }, auth: true });
      else await api<void>(path, { method: "DELETE", auth: true });
      load();
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  if (!consents && !error) return <Loading />;
  return (
    <Screen>
      <Stack.Screen options={{ title: "Privacy and consents" }} />
      <Title>Privacy and consents</Title>
      <Body muted>Choose what we may use your data for. You can change these at any time.</Body>
      {error && <Message tone="error">{error}</Message>}
      {consents?.map((consent) => (
        <Card key={consent.notice.purpose}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
            <Text style={{ flex: 1, fontWeight: "600", color: colors.text }}>{consent.notice.title}</Text>
            <Switch
              value={consent.granted}
              onValueChange={(on) => toggle(consent, on)}
              disabled={consent.notice.required && consent.granted}
            />
          </View>
          <Body muted>{consent.notice.summary}</Body>
          {consent.notice.required && <Body muted>Needed to run your account. Close the account to withdraw it.</Body>}
        </Card>
      ))}
    </Screen>
  );
}
