// DataClient interface — single implementation hits the FastAPI backend directly.
// Phase 1 ships against the real backend; the legacy mockClient plan lives here
// as commented notes so the surface stays swappable later.

import type {
  AlertsList,
  AlertDetail,
  DashboardSeriesResponse,
  DashboardSummary,
  DecisionWeights,
  DQEventRow,
  MetricsSnapshot,
  ScenarioEventRow,
  ScenarioResult,
  TickResult,
  UsersList,
} from "./types";

export interface TickRequest { hours?: number; provider?: string }
export type ScenarioKind = "bkash_surge" | "repeated_amount" | "structuring" | "rocket_delay" | "salary_day";
export interface InjectRequest {
  kind: ScenarioKind;
  label?: string;
  provider?: string;
  intended_severity?: "normal" | "low" | "high" | "critical";
  is_anomaly?: boolean;
  duration_minutes?: number;
}

export interface DataClient {
  getDashboard(agentId?: number): Promise<DashboardSummary>;
  getAlerts(opts?: { status?: string; severity?: string }): Promise<AlertsList>;
  getAlert(id: number): Promise<AlertDetail>;
  transitionAlert(id: number, action: "ack" | "review" | "resolve" | "escalate" | "decision" | "close", note?: string): Promise<AlertDetail>;
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
}

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const user = typeof window !== "undefined" ? window.localStorage.getItem("sa_user") || "agent" : "agent";
  headers.set("X-User", user);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) throw new Error(`API ${res.status} ${path}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const apiClient: DataClient = {
  getDashboard: (agentId = 1) => http(`/dashboard?agent_id=${agentId}`),
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
};

export const client: DataClient = apiClient;