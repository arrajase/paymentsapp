import { router, Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Text, View } from "react-native";

import { api } from "@/core/api/client";
import type { UpiLink } from "@/core/api/types";
import { Body, Button, Card, colors, Loading, Message, Row, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";
import { isCancelled, useStepUp } from "@/ui/step-up-prompt";

const MAX_LINKS = 3;

const FAILURE_REASONS: Record<string, string> = {
  rejected: "You declined the request at the bank.",
  expired: "The request expired at the bank.",
  revoked: "The request was withdrawn at the bank.",
  approval_timeout: "It wasn't approved at the bank in time.",
  linked_elsewhere: "This UPI ID is linked to another account.",
  consent_mismatch: "The bank's approval didn't match this request.",
};

/** Linked UPI IDs (plan M3). Opening the screen re-checks any that are waiting for approval at the bank. */
export default function UpiLinks() {
  const [links, setLinks] = useState<UpiLink[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const { confirm, prompt } = useStepUp();

  const load = useCallback(() => {
    setError(null);
    api<UpiLink[]>("/v1/upi/links", { auth: true }).then(setLinks, (e) => setError(errorMessage(e)));
  }, []);
  useFocusEffect(load);

  async function change(link: UpiLink, kind: "default" | "remove") {
    setError(null);
    try {
      const token =
        kind === "default"
          ? await confirm("upi_set_default", "Make this your default")
          : await confirm("upi_unlink", "Remove this UPI ID");
      setBusy(link.id);
      const headers = { "X-Step-Up-Token": token };
      if (kind === "default") {
        await api(`/v1/upi/links/${link.id}`, { method: "PATCH", body: { is_default: true }, auth: true, headers });
      } else {
        await api<void>(`/v1/upi/links/${link.id}`, { method: "DELETE", auth: true, headers });
      }
      load();
    } catch (e) {
      if (!isCancelled(e)) setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  if (!links && !error) return <Loading />;
  const open = links?.filter((l) => l.status === "pending" || l.status === "active") ?? [];

  return (
    <Screen>
      <Stack.Screen options={{ title: "Bank accounts" }} />
      <Title>Your UPI IDs</Title>
      <Body muted>Linked UPI IDs fund your wallet and receive withdrawals. The default one is used first.</Body>
      {error && <Message tone="error">{error}</Message>}
      {links?.length === 0 && <Body muted>{"You haven't linked a UPI ID yet."}</Body>}

      {links?.map((link) => (
        <Card key={link.id}>
          <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
            <Text style={{ fontWeight: "600", color: colors.text, flexShrink: 1 }}>{link.upi_id}</Text>
            {link.is_default && <Text style={{ color: colors.success, fontWeight: "600" }}>Default</Text>}
          </View>
          <Row label="Account holder" value={link.holder_name_masked} />
          {link.status === "active" && <Row label="Status" value="Linked" />}
          {link.status === "pending" && (
            <Body muted>Waiting for your approval on MockBank: open App Permissions and allow it with your UPI PIN.</Body>
          )}
          {link.status === "failed" && (
            <Message tone="error">{FAILURE_REASONS[link.status_reason ?? ""] ?? "This link didn't go through."}</Message>
          )}
          {link.status === "active" && !link.is_default && (
            <Button title="Make default" variant="secondary" onPress={() => change(link, "default")} loading={busy === link.id} />
          )}
          {(link.status === "active" || link.status === "pending") && (
            <Button
              title={link.status === "pending" ? "Cancel" : "Remove"}
              variant="secondary"
              onPress={() => change(link, "remove")}
              loading={busy === link.id}
            />
          )}
        </Card>
      ))}

      <Button
        title="Link a UPI ID"
        onPress={() => router.push("/link-upi")}
        disabled={open.length >= MAX_LINKS}
      />
      {open.some((l) => l.status === "pending") && <Button title="Check again" variant="secondary" onPress={load} />}
      <Body muted>
        {"Removing a UPI ID here stops the app using it. To withdraw the app's access at the bank too, use App Permissions on MockBank."}
      </Body>
      {prompt}
    </Screen>
  );
}
