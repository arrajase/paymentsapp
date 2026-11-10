import { Redirect, Stack } from "expo-router";

import { useSession } from "@/core/session";

export default function AuthLayout() {
  const { phase } = useSession();
  if (phase === "signed_in") return <Redirect href="/home" />;
  return <Stack screenOptions={{ headerShown: true, title: "" , headerShadowVisible: false }} />;
}
