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
  changes: { enabled?: boolean; mode?: string; config?: Record<string, string> };
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

export type SystemInfo = { environment: string; webhook_url: string };
