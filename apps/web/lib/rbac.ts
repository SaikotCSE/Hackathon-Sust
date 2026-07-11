// Role-based capability map — mirrors apps/api/app/services/auth.py:ROLE_PERMISSIONS.
// Single source of truth so the UI hides actions the backend would 403.

export type Role = "agent" | "ops" | "risk" | "provider" | "management";

// The capability name intentionally avoids the word "fraud" — it gates whether
// the role may issue the final compliance decision (i.e. close the case after
// review). The system does not declare fraud anywhere in the source; the
// capability simply enforces who has authority to mark a case closed.
export type Capability =
  | "can_see_own_agent_only"
  | "can_act_on_alerts"
  | "can_close_compliance_case"
  | "can_dispatch"
  | "can_see_all_providers"
  | "can_inject_scenario"
  | "can_view_metrics"
  | "can_reload_weights"
  | "can_view_cases";

const ROLE_PERMS: Record<Role, Capability[]> = {
  agent: [
    "can_see_own_agent_only",
    "can_act_on_alerts",
    "can_see_all_providers",
    "can_inject_scenario",
    "can_view_metrics",
    "can_view_cases",
  ],
  ops: [
    "can_act_on_alerts",
    "can_dispatch",
    "can_see_all_providers",
    "can_inject_scenario",
    "can_view_metrics",
    "can_view_cases",
  ],
  risk: [
    "can_act_on_alerts",
    "can_close_compliance_case",
    "can_see_all_providers",
    "can_view_metrics",
    "can_view_cases",
  ],
  provider: [
    "can_see_all_providers",
    "can_view_metrics",
  ],
  management: [
    "can_see_all_providers",
    "can_view_metrics",
  ],
};

export function can(role: Role | string | undefined, capability: Capability): boolean {
  if (!role) return false;
  const list = ROLE_PERMS[role as Role];
  if (!list) return false;
  return list.includes(capability);
}

// Per-action RBAC for the alert state machine — matches the server-side
// checks in apps/api/app/routers/alerts.py:transition_case.
const ACTION_ALLOWED: Record<string, Role[]> = {
  ack:      ["agent", "ops", "risk"],
  review:   ["agent", "ops", "risk"],
  resolve:  ["agent", "ops", "risk"],
  escalate: ["agent", "ops", "risk"],
  decision: ["risk"],
  close:    ["risk"],
};

export function canPerformAction(role: Role | string | undefined, action: string): boolean {
  if (!role) return false;
  return ACTION_ALLOWED[action]?.includes(role as Role) ?? false;
}

// Per-recommended-action RBAC for the Decision Intelligence panel.
// These map to the action keys in config/decision-weights.json and the
// orchestration output (Module 4). The UI filters and button-enables
// against this map so an agent, for example, never sees an "Assign Field
// Officer" button (they have no dispatch capability) and a provider only
// sees "Follow up with provider feed" (it's their feed that's degraded).
//
// Server-side enforcement is authoritative — see the matching block in
// apps/api/app/routers/alerts.py:execute_recommended_action.
const RECOMMENDED_ACTION_ALLOWED: Record<string, Role[]> = {
  notify_ops:            ["agent", "ops", "risk"],
  assign_field_officer:  ["ops", "risk"],           // dispatch is ops/risk only
  request_cash_support:  ["ops", "risk"],           // opens a provider ticket — ops/risk
  monitor:               ["agent", "ops", "risk", "provider", "management"],
  risk_review:           ["agent", "ops", "risk"],  // anyone with can_act_on_alerts can escalate
  data_quality_followup: ["provider", "ops", "risk"], // feed owner + ops/risk
};

export function canPerformRecommendedAction(
  role: Role | string | undefined,
  actionKey: string,
): boolean {
  if (!role) return false;
  return RECOMMENDED_ACTION_ALLOWED[actionKey]?.includes(role as Role) ?? false;
}

// Dashboard view key — which role-shaped component to render.
export function dashboardViewFor(role: Role | string | undefined): string {
  switch (role) {
    case "agent":      return "agent";
    case "ops":        return "ops";
    case "risk":       return "risk";
    case "provider":   return "provider";
    case "management": return "management";
    default:           return "agent";
  }
}

export const ROLE_LABELS: Record<string, string> = {
  agent:      "Multi-Provider Agent",
  ops:        "Operations_provider",
  risk:       "Risk analyst",
  provider:   "Provider View",
  management: "Management",
};

// Map seed usernames to a friendly "tier" label for the dashboard header.
export const USERNAME_TIER: Record<string, string> = {
  field:   "Operations_provider",
  ops:     "Operations_provider",
};

export function tierFor(username: string, role: string): string {
  if (role === "ops") return USERNAME_TIER[username] ?? "Operations_provider";
  return ROLE_LABELS[role] ?? "User";
}