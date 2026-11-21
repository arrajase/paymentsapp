// Shapes returned by the backend's /admin/v1 API (backend/src/paymentsapp/modules/*/schemas.py).

export type Role = "viewer" | "platform_admin" | "security_approver" | "operations" | "compliance" | "support";

export type Admin = {
  id: string;
  login_id: string;
  display_name: string;
  roles: Role[];
};

export type SignInResponse = {
  token: string;
  expires_at: string;
  idle_timeout_minutes: number;
  admin: Admin;
};

export type PluginField = { name: string; label: string; kind: string; value: string | null };

export type PluginSecret = {
  name: string;
  label: string;
  preview: string | null;
  set_at: string | null;
  set_by: string | null;
};

export type Plugin = {
  key: string;
  description: string;
  interfaces: string[];
  mode: string;
  allowed_modes: string[];
  enabled: boolean;
  version: number;
  updated_at: string;
  updated_by: string;
  config: PluginField[];
  secrets: PluginSecret[];
  pending_request_id: string | null;
};

export type AdminRef = { id: string; login_id: string; display_name: string };

export type RequestStatus = "PENDING" | "APPROVED" | "REJECTED" | "CANCELLED";

export type ChangeRequest = {
  id: string;
  target_type: string;
  target_key: string;
  status: RequestStatus;
  changes: { enabled?: boolean; mode?: string; config?: Record<string, string>; value?: boolean | number | string };
  secrets: Record<string, string>;
  reason: string;
  requested_by: AdminRef;
  requested_at: string;
  decided_by: AdminRef | null;
  decided_at: string | null;
  decision_note: string | null;
};

export type AuditEntry = {
  id: number;
  occurred_at: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  ip: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
};

export type ControlValue = boolean | number | string;

export type Control = {
  key: string;
  kind: "state" | "number" | "feature";
  description: string;
  value: ControlValue;
  source: string;
  default: ControlValue;
  allowed: ControlValue[];
  bounds: [number, number] | null;
  looser: "lower" | "higher" | null;
  client: boolean;
  expires_at: string | null;
  revert_value: ControlValue | null;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  pending_request_id: string | null;
};

export type DevMessage = {
  id: number;
  channel: "sms" | "email";
  to_address: string;
  purpose: string;
  subject: string | null;
  body: string;
  pushed_to_emulator: boolean;
  created_at: string;
};

export type DevInbox = { adb_found: boolean; messages: DevMessage[] };

export type SystemInfo = { environment: string; webhook_url: string };

export type HealthCheck = { checked_at: string; ok: boolean; latency_ms: number | null; detail: string };

export type PluginHealth = {
  key: string;
  enabled: boolean;
  mode: string;
  remote: boolean;
  circuit: "closed" | "open" | "half-open" | null;
  last_check: HealthCheck | null;
  events: {
    last_webhook_at: string | null;
    last_poll_at: string | null;
    last_poll_success_at: string | null;
    last_poll_error: string | null;
    received_24h: number;
  } | null;
};

export type ReconciliationRun = {
  id: number;
  provider: string;
  business_date: string;
  ran_at: string;
  ran_by: string;
  bank_balance: number | null;
  ledger_balance: number;
  matched: number;
  clean: boolean;
  issues: Record<string, string | number | null>[];
};

export type Reconciliation = {
  totals: { escrow: number; in_transit: number; wallets: number };
  runs: ReconciliationRun[];
};
