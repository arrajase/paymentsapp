import { Redirect } from "expo-router";

import { useSession } from "@/core/session";
import { Loading } from "@/ui/components";

/** Sends the customer to the right place for this phone's state. */
export default function Start() {
  const { phase } = useSession();
  if (phase === "loading") return <Loading />;
  if (phase === "signed_in") return <Redirect href="/home" />;
  if (phase === "locked") return <Redirect href="/unlock" />;
  return <Redirect href="/sign-in" />;
}
