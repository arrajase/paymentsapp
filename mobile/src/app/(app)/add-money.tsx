import * as Crypto from "expo-crypto";
import { router, Stack, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Linking } from "react-native";

import { api } from "@/core/api/client";
import type { AddMoneyStarted, Payment, UpiLink, Wallet } from "@/core/api/types";
import { Body, Button, Card, Field, Loading, Message, Row, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";
import { formatRupees, parseRupees } from "@/ui/money";

const POLL_MS = 3000;
const SETTLED: Payment["status"][] = ["SUCCESS", "FAILED"];

/**
 * Add money from the default linked UPI ID (plan M4, M5). The bank asks the customer to approve with their UPI PIN
 * on its website; this screen waits for the backend to confirm, and shows success only after it has.
 */
export default function AddMoney() {
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [source, setSource] = useState<UpiLink | null | undefined>(undefined);
  const [amount, setAmount] = useState("");
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const [payment, setPayment] = useState<AddMoneyStarted | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useFocusEffect(
    useCallback(() => {
      Promise.all([api<Wallet>("/v1/wallet", { auth: true }), api<UpiLink[]>("/v1/upi/links", { auth: true })]).then(
        ([w, links]) => {
          setWallet(w);
          setSource(links.find((l) => l.status === "active" && l.is_default) ?? null);
        },
        (e) => setError(errorMessage(e)),
      );
    }, []),
  );

  // Wait for the bank's answer. The backend checks with the bank each time it's asked.
  const waiting = payment !== null && !SETTLED.includes(payment.status);
  const paymentId = payment?.id;
  useEffect(() => {
    if (!waiting || !paymentId) return;
    const timer = setInterval(() => {
      api<Payment>(`/v1/payments/${paymentId}`, { auth: true }).then(
        (latest) => setPayment((current) => (current ? { ...current, ...latest } : current)),
        () => undefined, // keep waiting; a blip shouldn't end the wait
      );
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, paymentId]);

  const paise = parseRupees(amount);

  async function start() {
    if (paise === null) return;
    setError(null);
    setBusy(true);
    // One key per attempt: pressing again after a network error repeats the same request, never a second one.
    const key = requestKey ?? Crypto.randomUUID();
    setRequestKey(key);
    try {
      const started = await api<AddMoneyStarted>("/v1/wallet/add-money", {
        method: "POST",
        body: { amount: paise },
        auth: true,
        headers: { "Idempotency-Key": key },
      });
      setPayment(started);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!wallet && !error) return <Loading />;

  if (payment) {
    const done = payment.status === "SUCCESS";
    const failed = payment.status === "FAILED";
    return (
      <Screen>
        <Stack.Screen options={{ title: "Add money" }} />
        <Title>{done ? "Money added" : failed ? "Not added" : `Approve ${formatRupees(payment.amount)} on MockBank`}</Title>
        {waiting && (
          <>
            <Body>
              Sign in to MockBank as {payment.counterparty}, open Pending Requests, and approve the paymentsapp request
              with your UPI PIN.
            </Body>
            <Body muted>{"Your UPI PIN is entered only on the bank's website, never in this app."}</Body>
            {payment.approve_at && <Button title="Open MockBank" onPress={() => Linking.openURL(payment.approve_at!)} />}
            <Loading />
            <Body muted>
              {payment.status === "UNKNOWN"
                ? "The bank hasn't confirmed the request yet. We'll keep checking; nothing will be taken twice."
                : "Waiting for your approval. This screen updates by itself."}
            </Body>
          </>
        )}
        {done && <Message tone="success">{formatRupees(payment.amount)} was added to your wallet.</Message>}
        {failed && <Message tone="error">{payment.failure_message ?? "The payment didn't go through."}</Message>}
        {!waiting && <Button title="Back to wallet" onPress={() => router.replace("/wallet")} />}
        {waiting && <Button title="Close; it will finish in the background" variant="secondary" onPress={() => router.replace("/wallet")} />}
      </Screen>
    );
  }

  return (
    <Screen>
      <Stack.Screen options={{ title: "Add money" }} />
      <Title>Add money</Title>
      {error && <Message tone="error">{error}</Message>}
      {source === null && (
        <>
          <Message tone="error">Link a UPI ID first, to add money from it.</Message>
          <Button title="Link a UPI ID" onPress={() => router.replace("/link-upi")} />
        </>
      )}
      {source && wallet && (
        <>
          <Card>
            <Row label="From" value={source.upi_id} />
            <Row label="Balance now" value={formatRupees(wallet.balance)} />
            <Row label="You can add up to" value={formatRupees(wallet.can_add_now)} />
          </Card>
          <Field
            label="Amount (₹)"
            value={amount}
            onChangeText={(t) => {
              setAmount(t);
              setRequestKey(null); // a different amount is a different request
            }}
            keyboardType="decimal-pad"
            placeholder="500"
          />
          {amount !== "" && paise === null && <Body muted>Enter an amount in rupees, such as 500 or 250.50.</Body>}
          <Button
            title={paise ? `Add ${formatRupees(paise)}` : "Add money"}
            onPress={start}
            loading={busy}
            disabled={paise === null || paise > wallet.can_add_now}
          />
          {paise !== null && paise > wallet.can_add_now && <Body muted>{`That's over your limit: ${wallet.can_add_reason}.`}</Body>}
        </>
      )}
    </Screen>
  );
}
