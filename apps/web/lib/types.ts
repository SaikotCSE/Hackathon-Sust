// Shared TypeScript types — mirror the FastAPI backend's actual response shapes.

export type Severity = "normal" | "low" | "high" | "critical";
export type AlertStatus = "open" | "assigned" | "acknowledged" | "under_review" | "in_progress" | "resolved" | "escalated" | "risk_review" | "compliance_decision" | "closed";

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
  forecast_summary?: string;
  forecast_reasons: string[];
  data_quality: number;
  history: number[];
  // Fields surfaced for the per-role Provider Liquidity Card UI.
  recent_deltas?: number[];
  expected_outflow_next_hours?: number | null;
  current_demand_label?: "low" | "medium" | "high";
  shortage_eta_human?: string;
  // Degraded-state fields set by the server when this provider's own feed
  // is stale or has too few samples to project a shortage. The UI must
  // render these explicitly so a provider never mistakes "—" for "healthy".
  degraded?: boolean;
  degraded_reason?: string | null;
  forecast_state?: "projected" | "low_confidence" | "stable" | "depleted" | "stale" | "unavailable" | "insufficient_data";
  forecast_age_minutes?: number | null;
  forecast_generated_at?: string | null;
  projected_balance_8h?: number | null;
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
  initial_owner?: string;
  reasons?: string[];
  evidence?: Array<{ source: string; rule?: string; text: string }>;
  recommended_actions?: Array<{ key: string; label: string; weight: number }>;
  fused_explanation?: string;
  created_at: string;
}

export interface OperationalLiquiditySummary {
  physical_cash: number;
  provider_count: number;
  limiting_position: string | null;
  limiting_hours_to_shortage: number | null;
  shortage_eta_human: string;
  confidence: number;
  data_quality: number;
  pressure_label: string;
  fallback_active: boolean;
  non_convertible: true;
  notes: string[];
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
  aggregate?: OperationalLiquiditySummary;
  operational_contexts?: Array<{
    kind: string;
    provider: string;
    note: string;
    source: string;
    ends_at: string;
  }>;
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
    aggregate?: OperationalLiquiditySummary;
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
    assigned_to: string;
    assigned_contact_type: string;
    resolution_code: string | null;
    resolution_summary: string | null;
    closed_at: string | null;
    risk_recommendation: string | null;
    risk_recommendation_note: string | null;
    risk_recommended_by: string | null;
    risk_recommended_at: string | null;
    contacts: Record<string, { name: string; phone: string; label: string }> | null;
    notes: Array<{ ts: string; role: string; user: string; text: string }>;
    audit: Array<{ ts: string; from_state: string | null; to_state: string; actor: string; actor_role?: string; reason: string; owner_from?: string | null; owner_to?: string | null }>;
    timeline: Array<Record<string, any> & { ts: string; kind: "note" | "transition" }>;
    explanation: {
      summary?: string;
      factors?: string[];
      uncertainty?: string;
      recommended_next_step?: string;
      safe_recommendations?: string[];
      disclaimer?: string;
      source?: string;
      fallback_reason?: string | null;
    };
    explanation_provider: "gemini" | "grok" | "groq" | "fallback";
    explanation_model: string;
    explanation_status: "pending" | "generated" | "fallback";
    explanation_error: string | null;
    explanation_generated_at: string | null;
    explanation_language: "en" | "bn" | "banglish";
    latest_explanation_call: {
      id: number; provider: string; model: string; language: string; endpoint: string;
      status: string; latency_ms: number; error: string | null; created_at: string;
      request: Record<string, any>; response: Record<string, any>;
    } | null;
  };
}

export interface AlertsList {
  alerts: AlertDetail[];
}

export interface MetricsSnapshot {
  liquidity_mae_minutes: number | null;
  shortage_lead_time_minutes: number | null;
  anomaly_precision: number | null;
  anomaly_recall: number | null;
  false_positive_rate: number | null;
  explanation_coverage: number | null;
  api_latency_p50_ms: number | null;
  api_latency_p95_ms: number | null;
  confidence_delta_under_bad_data: number | null;
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

export interface CashSupportRequest {
  id: number;
  alert_id: number;
  agent_id: number;
  agent_code: string;
  agent_name: string;
  provider: string;
  requested_by: string;
  amount: number | null;
  forecast_balance: number | null;
  forecast_burn_rate_per_min: number | null;
  coverage_hours: number;
  target_balance: number | null;
  calculation: string;
  applied_amount: number;
  balance_after: number | null;
  applied_at?: string | null;
  note: string;
  status: "requested" | "acknowledged" | "approved" | "rejected" | "fulfilled";
  provider_note: string;
  created_at: string;
  updated_at: string;
}

export interface StakeholderNotification {
  id: number;
  alert_id: number;
  case_id: number;
  event: string;
  title: string;
  message: string;
  actor: string;
  created_at: string;
  read_at: string | null;
}

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
