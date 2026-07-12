// DataClient interface — single implementation hits the FastAPI backend directly.
// Phase 1 ships against the real backend; the legacy mockClient plan lives here
// as commented notes so the surface stays swappable later.

import type {
  AlertsList,
  AlertDetail,
  RecommendedActionResult,
  DashboardSeriesResponse,
  DashboardSummary,
  DecisionWeights,
  DQEventRow,
  MetricsSnapshot,
  ScenarioEventRow,
  ScenarioResult,
  TickResult,
  UsersList,
  CashSupportRequest,
  StakeholderNotification,
} from "./types";

export interface TickRequest { hours?: number; provider?: string }
export interface DashboardFilters {
  area?: string;
  provider?: string;
  agentId?: number;
  sinceMinutes?: number;
}
export type ScenarioKind = "bkash_surge" | "repeated_amount" | "structuring" | "rocket_delay" | "salary_day";
export interface InjectRequest {
  kind: ScenarioKind;
  label?: string;
  provider?: string;
  intended_severity?: "normal" | "low" | "high" | "critical";
  is_anomaly?: boolean;
  duration_minutes?: number;
}

// The six recommended-action keys surfaced in the Decision Intelligence
// panel. Mirrors config/decision-weights.json — keep in sync.
export type RecommendedActionKey =
  | "notify_ops"
  | "assign_field_officer"
  | "request_cash_support"
  | "monitor"
  | "risk_review"
  | "data_quality_followup";

export interface DataClient {
  getDashboard(agentId?: number, filters?: DashboardFilters): Promise<DashboardSummary>;
  getAlerts(opts?: { status?: string; severity?: string }): Promise<AlertsList>;
  getAlert(id: number): Promise<AlertDetail>;
  transitionAlert(id: number, action: "ack" | "review" | "start" | "resolve" | "escalate" | "close", note?: string): Promise<AlertDetail>;
  addReviewerNote(id: number, text: string): Promise<NonNullable<AlertDetail["case"]>>;
  regenerateExplanation(id: number, language?: "en" | "bn" | "banglish"): Promise<NonNullable<AlertDetail["case"]>>;
  coordinateCase(id: number, action: string, comment: string, target?: string): Promise<NonNullable<AlertDetail["case"]>>;
  recordRiskRecommendation(id: number, recommendation: string, comment: string): Promise<NonNullable<AlertDetail["case"]>>;
  executeRecommendedAction(id: number, actionKey: RecommendedActionKey, note?: string): Promise<RecommendedActionResult>;
  tickSimulation(req?: TickRequest): Promise<TickResult>;
  injectScenario(req: InjectRequest): Promise<ScenarioResult>;
  resolveDataQuality(provider: string): Promise<{ resolved: number; provider: string }>;
  getScenarios(): Promise<{ scenarios: ScenarioEventRow[] }>;
  getDataQualityEvents(): Promise<{ events: DQEventRow[] }>;
  getMetrics(): Promise<MetricsSnapshot>;
  getUsers(): Promise<UsersList>;
  getDecisionWeights(): Promise<DecisionWeights>;
  reloadDecisionWeights(): Promise<{ reloaded: boolean; providers: string[] }>;
  getDashboardSeries(agentId?: number, provider?: string): Promise<DashboardSeriesResponse>;
  getCashSupportRequests(): Promise<{ requests: CashSupportRequest[] }>;
  actOnCashSupport(id: number, action: "acknowledge" | "approve" | "reject" | "fulfil", note?: string): Promise<CashSupportRequest>;
  getNotifications(): Promise<{ notifications: StakeholderNotification[]; unread: number }>;
  markNotificationRead(id: number): Promise<StakeholderNotification>;
}

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const user = typeof window !== "undefined" ? window.localStorage.getItem("sa_user") || "agent" : "agent";
  headers.set("X-User", user);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (e: any) {
    // fetch() rejects with TypeError on network failure, CORS rejection,
    // or when the server kills the connection mid-response. Surface a
    // single friendly message instead of letting raw `Failed to fetch`
    // propagate into the UI stack.
    throw new Error(`Network error contacting ${BASE}${path}: ${e?.message || e}. Is the API running on ${BASE}?`);
  }
  if (!res.ok) throw new Error(`API ${res.status} ${path}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const apiClient: DataClient = {
  getDashboard: (agentId = 1, filters) => {
    const q = new URLSearchParams({ agent_id: String(agentId) });
    if (filters?.area) q.set("area", filters.area);
    if (filters?.provider) q.set("provider", filters.provider);
    if (filters?.agentId) q.set("mgr_agent", String(filters.agentId));
    if (filters?.sinceMinutes) q.set("since_minutes", String(filters.sinceMinutes));
    return http(`/dashboard?${q.toString()}`);
  },
  getAlerts: (opts) => {
    const q = new URLSearchParams();
    if (opts?.status) q.set("status", opts.status);
    if (opts?.severity) q.set("severity", opts.severity);
    const qs = q.toString();
    return http(`/alerts${qs ? `?${qs}` : ""}`);
  },
  getAlert: (id) => http(`/alerts/${id}`),
  transitionAlert: (id, action, note) =>
    http(`/alerts/${id}/transition`, {
      method: "POST",
      body: JSON.stringify({ action, note: note || "" }),
    }),
  addReviewerNote: (id, text) =>
    http(`/alerts/${id}/notes`, { method: "POST", body: JSON.stringify({ text }) }),
  regenerateExplanation: (id, language = "en") =>
    http(`/alerts/${id}/explanation/regenerate`, { method: "POST", body: JSON.stringify({ language }) }),
  coordinateCase: (id, action, comment, target) =>
    http(`/alerts/${id}/coordination`, { method: "POST", body: JSON.stringify({ action, comment, target }) }),
  recordRiskRecommendation: (id, recommendation, comment) =>
    http(`/alerts/${id}/risk-recommendation`, { method: "POST", body: JSON.stringify({ recommendation, comment }) }),
  executeRecommendedAction: (id, actionKey, note) =>
    http(`/alerts/${id}/action`, {
      method: "POST",
      body: JSON.stringify({ action_key: actionKey, note: note || "" }),
    }),
  tickSimulation: () => http(`/simulation/tick`, { method: "POST", body: "{}" }),
  injectScenario: (req) =>
    http(`/simulation/inject`, {
      method: "POST",
      body: JSON.stringify(req),
    }),
  resolveDataQuality: (provider) =>
    http(`/simulation/resolve-data-quality`, {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  getScenarios: () => http(`/simulation/scenarios`),
  getDataQualityEvents: () => http(`/simulation/data-quality`),
  getMetrics: () => http(`/metrics/snapshot`),
  getUsers: () => http(`/users`),
  getDecisionWeights: () => http(`/config/decision-weights`),
  reloadDecisionWeights: () => http(`/config/reload`, { method: "POST", body: "{}" }),
  getDashboardSeries: (agentId = 1, provider) =>
    http(`/dashboard/series?agent_id=${agentId}${provider ? `&provider=${provider}` : ""}`),
  getCashSupportRequests: () => http(`/cash-support`),
  actOnCashSupport: (id, action, note) =>
    http(`/cash-support/${id}/action`, {
      method: "POST",
      body: JSON.stringify({ action, note: note || "" }),
    }),
  getNotifications: () => http(`/notifications`),
  markNotificationRead: (id) => http(`/notifications/${id}/read`, { method: "POST", body: "{}" }),
};

export const client: DataClient = apiClient;
