import * as LocalAuthentication from "expo-local-authentication";
import { useCallback, useEffect, useState } from "react";
import { KeyboardAvoidingView, Modal, StyleSheet, View } from "react-native";

import { ApiError } from "@/core/api/client";
import type { StepUpAction } from "@/core/api/types";
import { stepUp, type StepUpMethod } from "@/core/security/step-up";
import { storage } from "@/core/storage";

import { Body, Button, colors, Field, Message, Title } from "./components";
import { errorMessage } from "./errors";

type Pending = { action: StepUpAction; title: string; resolve(token: string): void; reject(error: Error): void };

/**
 * Asks for the app PIN (or a fingerprint) before a sensitive action. `confirm` resolves with a step-up token, or
 * rejects with code CANCELLED if the customer backs out. Render `prompt` somewhere in the screen.
 */
export function useStepUp() {
  const [pending, setPending] = useState<Pending | null>(null);
  const [attempt, setAttempt] = useState(0);
  const confirm = useCallback(
    (action: StepUpAction, title: string) =>
      new Promise<string>((resolve, reject) => {
        setAttempt((n) => n + 1); // a fresh prompt each time: no PIN or error left over from the last one
        setPending({ action, title, resolve, reject });
      }),
    [],
  );
  const prompt = <StepUpPrompt key={attempt} pending={pending} onClose={() => setPending(null)} />;
  return { confirm, prompt };
}

export function isCancelled(error: unknown): boolean {
  return error instanceof ApiError && error.code === "CANCELLED";
}

function StepUpPrompt({ pending, onClose }: { pending: Pending | null; onClose(): void }) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canUseFingerprint, setCanUseFingerprint] = useState(false);

  useEffect(() => {
    Promise.all([storage.getBiometricUnlock(), LocalAuthentication.hasHardwareAsync(), LocalAuthentication.isEnrolledAsync()]).then(
      ([enabled, hardware, enrolled]) => setCanUseFingerprint(enabled && hardware && enrolled),
    );
  }, []);

  async function run(method: StepUpMethod) {
    if (!pending) return;
    setError(null);
    setBusy(true);
    try {
      const token = await stepUp(pending.action, method);
      onClose();
      pending.resolve(token);
    } catch (e) {
      setPin("");
      if (e instanceof ApiError && e.code === "PIN_LOCKED") {
        onClose();
        pending.reject(e);
      } else {
        setError(errorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }

  function cancel() {
    onClose();
    pending?.reject(new ApiError(0, "CANCELLED", "Cancelled."));
  }

  return (
    <Modal visible={pending !== null} transparent animationType="fade" onRequestClose={cancel}>
      <KeyboardAvoidingView behavior="padding" style={styles.backdrop}>
        <View style={styles.sheet}>
          <Title>{pending?.title ?? "Confirm"}</Title>
          <Body muted>Enter your app PIN to confirm.</Body>
          <Field
            label="App PIN"
            value={pin}
            onChangeText={(t) => setPin(t.replace(/\D/g, "").slice(0, 6))}
            keyboardType="number-pad"
            secureTextEntry
            maxLength={6}
            autoFocus
          />
          {error && <Message tone="error">{error}</Message>}
          <Button title="Confirm" onPress={() => run({ pin })} loading={busy} disabled={pin.length < 4} />
          {canUseFingerprint && <Button title="Use fingerprint" variant="secondary" onPress={() => run({ biometric: true })} />}
          <Button title="Cancel" variant="secondary" onPress={cancel} disabled={busy} />
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.4)" },
  sheet: { backgroundColor: colors.background, padding: 24, gap: 14, borderTopLeftRadius: 16, borderTopRightRadius: 16 },
});
