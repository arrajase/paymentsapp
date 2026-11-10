import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View, type TextInputProps } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

export const colors = {
  primary: "#4f46e5",
  primaryText: "#ffffff",
  text: "#18181b",
  muted: "#71717a",
  border: "#d4d4d8",
  background: "#fafafa",
  card: "#ffffff",
  error: "#be123c",
  errorBackground: "#fff1f2",
  success: "#047857",
  successBackground: "#ecfdf5",
};

export function Screen({ children }: { children: ReactNode }) {
  return (
    <SafeAreaView style={styles.safe}>
      <ScrollView contentContainerStyle={styles.screen} keyboardShouldPersistTaps="handled">
        {children}
      </ScrollView>
    </SafeAreaView>
  );
}

export function Title({ children }: { children: ReactNode }) {
  return <Text style={styles.title}>{children}</Text>;
}

export function Body({ children, muted }: { children: ReactNode; muted?: boolean }) {
  return <Text style={[styles.body, muted && styles.muted]}>{children}</Text>;
}

export function Field({ label, ...input }: TextInputProps & { label: string }) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput placeholderTextColor={colors.muted} style={styles.input} {...input} />
    </View>
  );
}

export function Button({
  title,
  onPress,
  loading,
  disabled,
  variant = "primary",
}: {
  title: string;
  onPress: () => void;
  loading?: boolean;
  disabled?: boolean;
  variant?: "primary" | "secondary";
}) {
  const primary = variant === "primary";
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.button,
        primary ? styles.buttonPrimary : styles.buttonSecondary,
        (disabled || loading) && styles.buttonDisabled,
        pressed && styles.buttonPressed,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={primary ? colors.primaryText : colors.primary} />
      ) : (
        <Text style={primary ? styles.buttonPrimaryText : styles.buttonSecondaryText}>{title}</Text>
      )}
    </Pressable>
  );
}

export function Message({ tone, children }: { tone: "error" | "success"; children: ReactNode }) {
  const error = tone === "error";
  return (
    <View style={[styles.message, { backgroundColor: error ? colors.errorBackground : colors.successBackground }]}>
      <Text style={{ color: error ? colors.error : colors.success }}>{children}</Text>
    </View>
  );
}

export function Card({ children }: { children: ReactNode }) {
  return <View style={styles.card}>{children}</View>;
}

export function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <View style={styles.row}>
      <Text style={styles.muted}>{label}</Text>
      <Text style={styles.rowValue}>{value}</Text>
    </View>
  );
}

export function Loading() {
  return (
    <View style={styles.loading}>
      <ActivityIndicator size="large" color={colors.primary} />
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  screen: { padding: 24, gap: 16 },
  title: { fontSize: 26, fontWeight: "700", color: colors.text, marginTop: 8 },
  body: { fontSize: 15, lineHeight: 22, color: colors.text },
  muted: { color: colors.muted },
  field: { gap: 6 },
  label: { fontSize: 14, fontWeight: "600", color: colors.text },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
    backgroundColor: colors.card,
    color: colors.text,
  },
  button: { borderRadius: 8, paddingVertical: 13, alignItems: "center" },
  buttonPrimary: { backgroundColor: colors.primary },
  buttonSecondary: { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.border },
  buttonDisabled: { opacity: 0.6 },
  buttonPressed: { opacity: 0.8 },
  buttonPrimaryText: { color: colors.primaryText, fontSize: 16, fontWeight: "600" },
  buttonSecondaryText: { color: colors.text, fontSize: 16, fontWeight: "600" },
  message: { borderRadius: 8, padding: 12 },
  card: { backgroundColor: colors.card, borderRadius: 12, borderWidth: 1, borderColor: "#e4e4e7", padding: 16, gap: 10 },
  row: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
  rowValue: { color: colors.text, fontWeight: "500", flexShrink: 1, textAlign: "right" },
  loading: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: colors.background },
});
