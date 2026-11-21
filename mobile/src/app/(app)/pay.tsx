import * as Crypto from "expo-crypto";
import { router, Stack, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/core/api/client";
import type { Payee, Payment, Recipient, UpiLink, Wallet } from "@/core/api/types";
import { Body, Button, Card, Field, Loading, Message, Row, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";
import { formatRupees, parseRupees } from "@/ui/money";
import { isCancelled, useStepUp } from "@/ui/step-up-prompt";

type Kind = "merchant" | "withdrawal" | "p2p";

const COPY: Record<Kind, { title: string; verb: string; done: string }> = {
  merchant: { title: "Pay a UPI ID", verb: "Pay", done: "Paid" },
  withdrawal: { title: "Withdraw to bank", verb: "Withdraw", done: "Withdrawn" },
  p2p: { title: "Send to a wallet", verb: "Send", done: "Sent" },
};
const POLL_MS = 3000;

/**
 * Money out of the wallet (plan M5): pay any UPI ID, withdraw to your own linked UPI ID, or send to another
 * customer's wallet by mobile number. Every payment is confirmed with the app PIN (step-up).
 */
export default function Pay() {
  const { kind = "merchant" } = useLocalSearchParams<{ kind: Kind }>();
  const copy = COPY[kind];
  const { confirm, prompt } = useStepUp();
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [link, setLink] = useState<UpiLink | null | undefined>(undefined);
  const [target, setTarget] = useState("");
  const [payee, setPayee] = useState<Payee | null>(null);
  const [recipient, setRecipient] = useState<Recipient | null>(null);
  const [amount, setAmount] = useState("");
  // One key per attempt, kept with its step-up token, so a retry after a network error repeats the same payment.
  const [attempt, setAttempt] = useState<{ key: string; token: string } | null>(null);
  const [result, setResult] = useState<Payment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useFocusEffect(
    useCallback(() => {
      Promise.all([api<Wallet>("/v1/wallet", { auth: true }), api<UpiLink[]>("/v1/upi/links", { auth: true })]).then(
        ([w, links]) => {
          setWallet(w);
          setLink(links.find((l) => l.status === "active" && l.is_default) ?? null);
        },
        (e) => setError(errorMessage(e)),
      );
    }, []),
  );

  // A bank payment can come back UNKNOWN; keep asking until the backend has the bank's answer.
  const settling = result !== null && (result.status === "UNKNOWN" || result.status === "CHECKED");
  const resultId = result?.id;
  useEffect(() => {
    if (!settling || !resultId) return;
    const timer = setInterval(() => {
      api<Payment>(`/v1/payments/${resultId}`, { auth: true }).then(setResult, () => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [settling, resultId]);

  function changed() {
    setAttempt(null);
    setError(null);
  }

  async function check() {
    setError(null);
    setBusy(true);
    try {
      if (kind === "p2p") {
        setRecipient(await api<Recipient>("/v1/payments/recipient", { method: "POST", body: { mobile: target }, auth: true }));
      } else {
        setPayee(await api<Payee>("/v1/payments/payee", { method: "POST", body: { upi_id: target }, auth: true }));
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const paise = parseRupees(amount);

  async function send() {
    if (paise === null) return;
    setError(null);
    try {
      const current = attempt ?? { key: Crypto.randomUUID(), token: await confirm("payment", `${copy.verb} ${formatRupees(paise)}`) };
      setAttempt(current);
      setBusy(true);
      const body =
        kind === "p2p"
          ? { kind, amount: paise, recipient_mobile: target }
          : kind === "merchant"
            ? { kind, amount: paise, payee_upi_id: payee?.upi_id }
            : { kind, amount: paise };
      setResult(
        await api<Payment>("/v1/payments", {
          method: "POST",
          body,
          auth: true,
          headers: { "Idempotency-Key": current.key, "X-Step-Up-Token": current.token },
        }),
      );
    } catch (e) {
      if (!isCancelled(e)) setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  if (!wallet && !error) return <Loading />;

  if (result) {
    const done = result.status === "SUCCESS";
    return (
      <Screen>
        <Stack.Screen options={{ title: copy.title }} />
        <Title>{done ? copy.done : result.status === "FAILED" ? "Didn't go through" : "Checking with the bank"}</Title>
        {done && <Message tone="success">{`${formatRupees(result.amount)} to ${result.counterparty}.`}</Message>}
        {result.status === "FAILED" && <Message tone="error">{result.failure_message ?? "The payment didn't go through."}</Message>}
        {settling && (
          <>
            <Loading />
            <Body muted>
              {"The bank hasn't confirmed yet. The money is held, not lost: it either reaches the payee or comes back to your wallet, and it can't be paid twice."}
            </Body>
          </>
        )}
        <Button title="Back to wallet" onPress={() => router.replace("/wallet")} />
      </Screen>
    );
  }

  const ready =
    paise !== null &&
    wallet !== null &&
    paise <= wallet.balance &&
    (kind === "withdrawal" ? !!link : kind === "p2p" ? !!recipient?.can_receive : !!payee?.active);

  return (
    <Screen>
      <Stack.Screen options={{ title: copy.title }} />
      <Title>{copy.title}</Title>
      {error && <Message tone="error">{error}</Message>}

      {kind === "withdrawal" &&
        (link === null ? (
          <Message tone="error">Link a UPI ID first, to withdraw to it.</Message>
        ) : (
          link && (
            <Card>
              <Row label="To" value={link.upi_id} />
              <Row label="Account holder" value={link.holder_name_masked} />
            </Card>
          )
        ))}

      {kind !== "withdrawal" && (
        <>
          <Field
            label={kind === "p2p" ? "Their mobile number" : "UPI ID"}
            value={target}
            onChangeText={(t) => {
              setTarget(t);
              setPayee(null);
              setRecipient(null);
              changed();
            }}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType={kind === "p2p" ? "phone-pad" : "email-address"}
            placeholder={kind === "p2p" ? "+91 90000 00002" : "shop@okmockbank"}
          />
          {!payee && !recipient && (
            <Button title={kind === "p2p" ? "Find" : "Check"} variant="secondary" onPress={check} loading={busy} disabled={target.trim().length < 5} />
          )}
          {payee && (
            <Card>
              <Row label="UPI ID" value={payee.upi_id} />
              <Row label="Name" value={payee.name_masked} />
              {!payee.active && <Message tone="error">This UPI ID isn&apos;t active.</Message>}
            </Card>
          )}
          {recipient && (
            <Card>
              <Row label="Mobile" value={recipient.mobile_masked} />
              <Row label="Name" value={recipient.name_masked} />
              {!recipient.can_receive && <Message tone="error">They need full KYC to receive wallet transfers.</Message>}
            </Card>
          )}
        </>
      )}

      {wallet && (
        <>
          <Field
            label="Amount (₹)"
            value={amount}
            onChangeText={(t) => {
              setAmount(t);
              changed();
            }}
            keyboardType="decimal-pad"
            placeholder="100"
          />
          <Body muted>Wallet balance: {formatRupees(wallet.balance)}</Body>
          {paise !== null && paise > wallet.balance && <Body muted>That&apos;s more than your wallet balance.</Body>}
          <Button title={paise ? `${copy.verb} ${formatRupees(paise)}` : copy.verb} onPress={send} loading={busy} disabled={!ready} />
        </>
      )}
      {prompt}
    </Screen>
  );
}
