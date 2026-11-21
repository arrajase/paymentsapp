import { router, Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Text, View } from "react-native";

import { api } from "@/core/api/client";
import type { Payment, PaymentKind, Wallet } from "@/core/api/types";
import { Body, Button, Card, colors, Loading, Message, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";
import { formatRupees } from "@/ui/money";

const STATUS_LABELS: Record<Payment["status"], string> = {
  CREATED: "Starting",
  CHECKED: "Starting",
  AWAITING_AUTH: "Waiting for your approval",
  UNKNOWN: "Checking with the bank",
  SUCCESS: "Done",
  FAILED: "Failed",
};

function describe(p: Payment): string {
  const labels: Record<PaymentKind, string> = {
    add_money: `Added from ${p.counterparty}`,
    merchant: `Paid to ${p.counterparty}`,
    withdrawal: `Withdrawn to ${p.counterparty}`,
    p2p: p.direction === "in" ? `Received from ${p.counterparty}` : `Sent to ${p.counterparty}`,
  };
  return labels[p.kind];
}

export default function WalletScreen() {
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [history, setHistory] = useState<Payment[]>([]);
  const [error, setError] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      setError(null);
      Promise.all([api<Wallet>("/v1/wallet", { auth: true }), api<Payment[]>("/v1/wallet/transactions", { auth: true })]).then(
        ([w, h]) => {
          setWallet(w);
          setHistory(h);
        },
        (e) => setError(errorMessage(e)),
      );
    }, []),
  );

  if (!wallet && !error) return <Loading />;
  return (
    <Screen>
      <Stack.Screen options={{ title: "Wallet" }} />
      {error && <Message tone="error">{error}</Message>}
      {wallet && (
        <>
          <Card>
            <Body muted>Wallet balance</Body>
            <Text style={{ fontSize: 34, fontWeight: "700", color: colors.text }}>{formatRupees(wallet.balance)}</Text>
            {wallet.max_balance !== null && <Body muted>Your wallet can hold up to {formatRupees(wallet.max_balance)}.</Body>}
          </Card>
          {wallet.enabled ? (
            <>
              <Button title="Add money" onPress={() => router.push("/add-money")} disabled={wallet.can_add_now <= 0} />
              <Button
                title="Pay a UPI ID"
                variant="secondary"
                onPress={() => router.push({ pathname: "/pay", params: { kind: "merchant" } })}
                disabled={wallet.balance <= 0}
              />
              {wallet.p2p_transfers && (
                <Button
                  title="Send to a wallet"
                  variant="secondary"
                  onPress={() => router.push({ pathname: "/pay", params: { kind: "p2p" } })}
                  disabled={wallet.balance <= 0}
                />
              )}
              {wallet.withdrawals && (
                <Button
                  title="Withdraw to bank"
                  variant="secondary"
                  onPress={() => router.push({ pathname: "/pay", params: { kind: "withdrawal" } })}
                  disabled={wallet.balance <= 0}
                />
              )}
              {!wallet.p2p_transfers && <Body muted>Complete full KYC to send money to other wallets and withdraw to your bank.</Body>}
            </>
          ) : (
            <Button title="Complete minimum KYC" onPress={() => router.push("/kyc")} />
          )}
          {wallet.enabled && wallet.can_add_now <= 0 && <Body muted>{`You can't add money right now: ${wallet.can_add_reason}.`}</Body>}

          <Title>History</Title>
          {history.length === 0 && <Body muted>No transactions yet.</Body>}
          {history.map((p) => (
            <Card key={p.id}>
              <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 12 }}>
                <Text style={{ color: colors.text, fontWeight: "600", flexShrink: 1 }}>{describe(p)}</Text>
                <Text style={{ color: p.direction === "in" && p.status === "SUCCESS" ? colors.success : colors.text, fontWeight: "600" }}>
                  {p.status === "SUCCESS" ? (p.direction === "in" ? "+" : "−") : ""}
                  {formatRupees(p.amount)}
                </Text>
              </View>
              <Body muted>
                {STATUS_LABELS[p.status]} · {new Date(p.created_at).toLocaleString("en-IN")}
              </Body>
              {p.failure_message && <Body muted>{p.failure_message}</Body>}
            </Card>
          ))}
        </>
      )}
    </Screen>
  );
}
