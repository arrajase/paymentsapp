import { Redirect, Stack } from "expo-router";

import { useSession } from "@/core/session";

export default function AppLayout() {
  const { phase } = useSession();
  // Session ended (signed out, expired, or replaced by another device): back to the start screen.
  if (phase !== "signed_in") return <Redirect href="/" />;
  return <Stack screenOptions={{ headerShadowVisible: false }} />;
}
