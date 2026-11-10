import * as ScreenCapture from "expo-screen-capture";
import { useEffect } from "react";

import { useSession } from "@/core/session";

/**
 * Call on screens showing a PIN, OTP, balance or KYC data (plan 7.1). Blocks screenshots and blanks the app-switcher
 * preview while the screen is open, when the server's screen_capture_protection control is set to enforce.
 */
export function useSensitiveScreen(name: string): void {
  const { appConfig } = useSession();
  const enforce = appConfig?.controls.screen_capture_protection === "enforce";
  useEffect(() => {
    if (!enforce) return;
    ScreenCapture.preventScreenCaptureAsync(name);
    return () => {
      ScreenCapture.allowScreenCaptureAsync(name);
    };
  }, [enforce, name]);
}
