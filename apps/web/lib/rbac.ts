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
  ops:        "Provider Operations",
  risk:       "Risk / Compliance Analyst",
  provider:   "Provider View",
  management: "Management",
};

// Map seed usernames to a friendly "tier" label for the dashboard header.
export const USERNAME_TIER: Record<string, string> = {
  field:   "Field Officer",
  manager: "Area Manager",
  ops:     "Provider Operations",
};

export function tierFor(username: string, role: string): string {
  if (role === "ops") return USERNAME_TIER[username] ?? "Network Coordination";
  return ROLE_LABELS[role] ?? "User";
}