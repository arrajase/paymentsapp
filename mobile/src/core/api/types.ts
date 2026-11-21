// Shapes returned by the backend's customer API (/v1).

export type IdentifierKind = "email" | "mobile";

export type OtpRequested = { challenge_id: string; sent_to: string; expires_at: string; resend_after_seconds: number };

export type Notice = { purpose: string; version: string; title: string; summary: string; required: boolean };

export type CodeVerified = {
  enrolment_token: string;
  expires_at: string;
  next_step: "create_account" | "set_pin" | "enter_pin";
  terms: Notice | null;
};

export type Tokens = {
  access_token: string;
  access_expires_at: string;
  refresh_token: string;
  refresh_expires_at: string;
};

export type Enrolled = {
  customer_id: string;
  device_id: string;
  new_customer: boolean;
  replaced_device: boolean;
  tokens: Tokens;
};

export type Challenge = { challenge_id: string; nonce: string; expires_at: string; sign: string };

export type Kyc = { tier: "T0" | "T1" | "T2"; full_name: string | null; date_of_birth: string | null };

export type Me = {
  customer_id: string;
  identifiers: { kind: IdentifierKind; masked: string; verified_at: string }[];
  device: { id: string; platform: string; label: string | null; hardware_backed: boolean; cooling_until: string | null };
  kyc: Kyc;
  pin_set: boolean;
};

export type Consent = {
  notice: Notice;
  granted: boolean;
  accepted_version: string | null;
  granted_at: string | null;
  needs_acceptance: boolean;
};

export type AppConfig = {
  environment: string;
  controls: Record<string, string | number | boolean>;
  generated_at: string;
};

export type StepUpAction = "upi_link" | "upi_unlink" | "upi_set_default" | "payment";

export type StepUp = { step_up_token: string; action: StepUpAction; expires_at: string };

export type UpiCandidate = {
  upi_id: string;
  holder_name_masked: string;
  name_matches: boolean;
  found_by_mobile: boolean | null; // null: not checked (sim_binding off)
  can_link: boolean;
  problem_code: string | null;
  problem: string | null;
};

export type UpiDiscover = { found: boolean; candidate: UpiCandidate | null };

export type UpiLink = {
  id: string;
  upi_id: string;
  holder_name_masked: string;
  status: "pending" | "active" | "failed" | "removed";
  status_reason: string | null;
  is_default: boolean;
  created_at: string;
  activated_at: string | null;
  consent_expires_at: string;
};

export type UpiLinkCreated = UpiLink & { approve_at: string | null };

export type Wallet = {
  balance: number; // paise
  currency: "INR";
  tier: "T0" | "T1" | "T2";
  enabled: boolean;
  max_balance: number | null;
  can_add_now: number;
  can_add_reason: string;
  merchant_payments: boolean;
  p2p_transfers: boolean;
  withdrawals: boolean;
};

export type PaymentStatus = "CREATED" | "CHECKED" | "AWAITING_AUTH" | "UNKNOWN" | "SUCCESS" | "FAILED";

export type PaymentKind = "add_money" | "merchant" | "withdrawal" | "p2p";

export type Payment = {
  id: string;
  kind: PaymentKind;
  direction: "in" | "out";
  status: PaymentStatus;
  amount: number; // paise
  currency: "INR";
  counterparty: string;
  failure_reason: string | null;
  failure_message: string | null;
  created_at: string;
  completed_at: string | null;
  expires_at: string | null;
};

export type AddMoneyStarted = Payment & { approve_at: string | null };

export type Payee = { upi_id: string; name_masked: string; active: boolean };

export type Recipient = { mobile_masked: string; name_masked: string; can_receive: boolean };
