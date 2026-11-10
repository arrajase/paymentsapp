import * as SecureStore from "expo-secure-store";

// Everything sensitive lives in SecureStore (Keystore-encrypted), never in AsyncStorage (plan 7.1).
// The access token stays in memory only; the refresh token and device ID survive restarts.
const DEVICE_ID = "device_id";
const REFRESH_TOKEN = "refresh_token";
const BIOMETRIC_UNLOCK = "biometric_unlock";

export const storage = {
  getDeviceId: () => SecureStore.getItemAsync(DEVICE_ID),
  setDeviceId: (id: string) => SecureStore.setItemAsync(DEVICE_ID, id),

  getRefreshToken: () => SecureStore.getItemAsync(REFRESH_TOKEN),
  setRefreshToken: (token: string) => SecureStore.setItemAsync(REFRESH_TOKEN, token),
  clearRefreshToken: () => SecureStore.deleteItemAsync(REFRESH_TOKEN),

  getBiometricUnlock: async () => (await SecureStore.getItemAsync(BIOMETRIC_UNLOCK)) === "on",
  setBiometricUnlock: (on: boolean) => SecureStore.setItemAsync(BIOMETRIC_UNLOCK, on ? "on" : "off"),

  /** Forget this device entirely: used when the backend says the device is no longer registered. */
  async forgetDevice() {
    await Promise.all([
      SecureStore.deleteItemAsync(DEVICE_ID),
      SecureStore.deleteItemAsync(REFRESH_TOKEN),
      SecureStore.deleteItemAsync(BIOMETRIC_UNLOCK),
    ]);
  },
};
