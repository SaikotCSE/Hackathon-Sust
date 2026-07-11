// Shared TypeScript types — mirror the FastAPI backend's actual response shapes.

export type Severity = "normal" | "low" | "high" | "critical";
export type AlertStatus = "open" | "assigned" | "acknowledged" | "under_review" | "resolved" | "escalated" | "compliance_decision" | "closed";

export interface Principal {
  username: string;
  display_name: string;
  role: string;
  provider?: string | null;
}

export interface DashboardProvider {
  provider: string;
  balance: number | null;
  health: "normal" | "low" | "high" | "critical" | "unknown";
  burn_rate_per_min: number;
  hours_to_shortage: number | null;
  forecast_confidence: number;
  forecast_reasons: string[];
  data_quality: number;
  history: number[];
  // Fields surfaced for the per-role Provider Liquidity Card UI.
  recent_deltas?: number[];
  expected_outflow_next_hours?: number | null;
  current_demand_label?: "low" | "medium" | "high";
  shortage_eta_human?: string;
}

export interface DashboardAlert {
  id: number;
  provider: string | null;
  severity: Severity;
  priority_score: number;
  title: string;
  summary: string;
  confidence: number;
  status: AlertStatus;
  owner_role: string;
  owner_label: string;
  created_at: string;
}

export interface DashboardSummary {
  // agent view (existing fields, kept for backwards compatibility)
  agent_id?: number;
  agent_code?: string;
  display_name?: string;
  area?: string;
  physical_cash?: number;
  overall_score?: number;
  overall_reason?: string;
  providers?: DashboardProvider[];
  alerts?: DashboardAlert[];
  // ops / provider views
  per_agent?: Array<{
    agent_id: number;
    agent_code: string;
    display_name: string;
    area: string;
    physical_cash: number;
    overall_score: number;
    overall_reason: string;
    providers: DashboardProvider[];
    alerts: DashboardAlert[];
  }>;
  // risk view
  queue?: Array<DashboardAlert & {
    reasons: string[];
    evidence: Array<{ source: string; rule?: string; text: string }>;
  }>;
  // management view
  areas?: Array<{
    area: string;
    agents: number;
    open_alerts: number;
    critical_alerts: number;
    avg_pressure: number;
    agents_detail: Array<{
      agent_id: number;
      agent_code: string;
      display_name: string;
      overall_score: number;
      open_alerts: number;
    }>;
  }>;
  // envelope
  view: "agent" | "ops" | "risk" | "provider" | "management";
  scope: Record<string, any>;
  principal?: Principal;
}

export interface AlertDetail {
  id: number;
  agent_id: number;
  provider: string | null;
  severity: Severity;
  priority_score: number;
  title: string;
  summary: string;
  reasons: string[];
  evidence: Array<{ source: string; rule?: string; text: string }>;
  confidence: number;
  recommended_actions: Array<{ key: string; label: string; weight: number }>;
  fused_explanation: string;
  owner_role: string;
  owner_label: string;
  initial_owner: string;
  status: AlertStatus;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  resolution_reason: string | null;
  case?: {
    id: number;
    state: string;
    owner_role: string;
    owner_label: string;
    notes: Array<{ ts: string; role: string; user: string; text: string }>;
    audit: Array<{ ts: string; from_state: string | null; to_state: string; actor: string; reason: string }>;
  };
}

export interface AlertsList {
  alerts: AlertDetail[];
}

export interface MetricsSnapshot {
  liquidity_mae_minutes: number;
  shortage_lead_time_minutes: number;
  anomaly_precision: number;
  anomaly_recall: number;
  false_positive_rate: number;
  explanation_coverage: number;
  api_latency_p50_ms: number;
  api_latency_p95_ms: number;
  confidence_delta_under_bad_data: number;
  priority_classification_alignment: number | null;
  alert_count: number;
  anomaly_event_count: number;
  generated_at: string;
}

export interface DashboardSeriesPoint { ts: string; balance: number }
export interface DashboardSeries {
  provider: string;
  points: DashboardSeriesPoint[];
  burn_rate_per_min: number;
  hours_to_shortage: number | null;
  confidence: number;
}
export interface DashboardSeriesResponse {
  agent_id: number;
  series: DashboardSeries[];
}

export interface TickResult {
  ticked: number;
  new_alerts: Array<{ id: number; severity: Severity; title: string; provider: string | null }>;
  data_quality: Record<string, number>;
  latency_ms: number;
}

export interface ScenarioResult {
  scenario_event_id: number;
  kind: string;
  intended_severity: string;
}

export interface UserInfo {
  username: string;
  display_name: string;
  role: string;
  provider?: string | null;
  area?: string | null;
}

export interface UsersList { users: UserInfo[]; }

export interface DecisionWeights {
  providers: Record<string, Record<string, number>>;
}

export interface ScenarioEventRow {
  id: number;
  agent_id: number;
  provider: string;
  kind: string;
  intended_severity: string;
  is_anomaly_ground_truth: boolean;
  note: string;
  injected_at: string;
}

export interface DQEventRow {
  id: number;
  provider: string;
  issue: string;
  note: string;
  started_at: string;
  resolved_at: string | null;
}