import * as LocalAuthentication from "expo-local-authentication";
import { router, Stack, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Switch, Text, View } from "react-native";

import { api } from "@/core/api/client";
import type { Me, Wallet } from "@/core/api/types";
import { useSession } from "@/core/session";
import { storage } from "@/core/storage";
import { Body, Button, Card, colors, Loading, Message, Row, Screen, Title } from "@/ui/components";
import { errorMessage } from "@/ui/errors";
import { formatRupees } from "@/ui/money";

const TIER_LABELS = { T0: "Registered (no wallet yet)", T1: "Minimum KYC", T2: "Full KYC" } as const;

export default function Home() {
  const { signOut } = useSession();
  const [me, setMe] = useState<Me | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fingerprint, setFingerprint] = useState({ available: false, on: false });

  const load = useCallback(() => {
    setError(null);
    api<Me>("/v1/me", { auth: true }).then(setMe, (e) => setError(errorMessage(e)));
    api<Wallet>("/v1/wallet", { auth: true }).then(setWallet, () => setWallet(null));
    Promise.all([LocalAuthentication.hasHardwareAsync(), LocalAuthentication.isEnrolledAsync(), storage.getBiometricUnlock()]).then(
      ([hardware, enrolled, on]) => setFingerprint({ available: hardware && enrolled, on }),
    );
  }, []);
  useFocusEffect(load);

  async function toggleFingerprint(on: boolean) {
    if (on) {
      const result = await LocalAuthentication.authenticateAsync({ promptMessage: "Confirm to turn on fingerprint unlock" });
      if (!result.success) return;
    }
    await storage.setBiometricUnlock(on);
    setFingerprint((f) => ({ ...f, on }));
  }

  if (!me && !error) return <Loading />;
  const mobile = me?.identifiers.find((i) => i.kind === "mobile");
  const email = me?.identifiers.find((i) => i.kind === "email");

  return (
    <Screen>
      <Stack.Screen options={{ title: "paymentsapp" }} />
      <Title>{me?.kyc.full_name ? `Hello, ${me.kyc.full_name.split(" ")[0]}` : "Hello"}</Title>
      {error && <Message tone="error">{error}</Message>}
      {me && (
        <>
          <Card>
            <Text style={{ fontWeight: "600", color: colors.text }}>Your account</Text>
            <Row label="Mobile" value={mobile?.masked ?? "not added"} />
            <Row label="Email" value={email?.masked ?? "not added"} />
            <Row label="KYC" value={TIER_LABELS[me.kyc.tier]} />
            {me.device.cooling_until && (
              <Body muted>New device: some limits are lower until {new Date(me.device.cooling_until).toLocaleString("en-IN")}.</Body>
            )}
          </Card>

          {wallet?.enabled && (
            <Card>
              <Body muted>Wallet balance</Body>
              <Text style={{ fontSize: 28, fontWeight: "700", color: colors.text }}>{formatRupees(wallet.balance)}</Text>
              <Button title="Wallet" onPress={() => router.push("/wallet")} />
            </Card>
          )}

          {me.kyc.tier !== "T2" && (
            <Button title={me.kyc.tier === "T0" ? "Complete minimum KYC" : "Upgrade to full KYC"} onPress={() => router.push("/kyc")} />
          )}
          {(!mobile || !email) && (
            <Button
              title={mobile ? "Add an email" : "Add a mobile number"}
              variant="secondary"
              onPress={() => router.push({ pathname: "/add-identifier", params: { kind: mobile ? "email" : "mobile" } })}
            />
          )}
          {me.kyc.tier !== "T0" && <Button title="Bank accounts (UPI)" onPress={() => router.push("/upi")} />}
          <Button title="Privacy and consents" variant="secondary" onPress={() => router.push("/consents")} />

          {fingerprint.available && (
            <Card>
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                <Text style={{ color: colors.text }}>Unlock with fingerprint</Text>
                <Switch value={fingerprint.on} onValueChange={toggleFingerprint} />
              </View>
            </Card>
          )}
        </>
      )}
      <Button title="Sign out" variant="secondary" onPress={signOut} />
    </Screen>
  );
}
