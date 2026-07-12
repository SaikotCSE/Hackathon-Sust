"use client";

import React, { useEffect, useMemo, useState } from "react";
import type {
  CashSupportRequest,
  DashboardAlert,
  DashboardSummary,
} from "../lib/types";
import { Card } from "./Primitives";
import { client, type RecommendedActionKey } from "../lib/client";
import { usePrincipal } from "./PrincipalProvider";
import { canPerformRecommendedAction } from "../lib/rbac";

const CLOSED_STATUSES = new Set<DashboardAlert["status"]>(["resolved", "closed"]);
const ACTIVE_SUPPORT_STATUSES = new Set<CashSupportRequest["status"]>([
  "requested",
  "acknowledged",
  "approved",
]);

type RankedAction = { key: string; label: string; weight: number };
type DecisionItem = {
  key: string;
  kind: "alert" | "forecast";
  alertId?: number;
  severity: string;
  priorityScore: number;
  provider: string | null;
  title: string;
  summary: string;
  reasons: string[];
  confidence: number;
  recommended: RankedAction[];
  ownerRole: string;
  ownerLabel: string;
  status: string;
};

const ACTION_STYLE: Record<string, { label: string; color: string; hint: string; icon: string; owner: string }> = {
  notify_ops: {
    label: "Notify Operations",
    color: "#0ea5e9",
    hint: "Send a durable hand-off to Provider Operations for triage.",
    icon: "📣",
    owner: "Agent or Operations",
  },
  assign_field_officer: {
    label: "Assign Field Officer",
    color: "#16a34a",
    hint: "Dispatch a field officer to verify service readiness and stock.",
    icon: "🚚",
    owner: "Operations",
  },
  request_cash_support: {
    label: "Request Provider Liquidity Support",
    color: "#ea580c",
    hint: "Create a forecast-sized request for this provider only. No funds move automatically.",
    icon: "💵",
    owner: "Agent or Operations",
  },
  monitor: {
    label: "Record Monitoring",
    color: "#64748b",
    hint: "Record the decision to keep watching without changing the case state.",
    icon: "👀",
    owner: "Current stakeholder",
  },
  risk_review: {
    label: "Escalate to Risk Review",
    color: "#7c3aed",
    hint: "Request independent evidence review; this is not a wrongdoing determination.",
    icon: "⚖️",
    owner: "Operations",
  },
  data_quality_followup: {
    label: "Follow Up on Provider Feed",
    color: "#0891b2",
    hint: "Route the degraded feed to the responsible provider integration team.",
    icon: "🔌",
    owner: "Provider or Operations",
  },
};

const SEV_BG: Record<string, string> = {
  critical: "#fee2e2",
  high: "#ffedd5",
  low: "#fef9c3",
  normal: "#dcfce7",
};
const SEV_FG: Record<string, string> = {
  critical: "#991b1b",
  high: "#9a3412",
  low: "#854d0e",
  normal: "#166534",
};

function sevBg(severity?: string) {
  return SEV_BG[severity ?? "normal"] ?? SEV_BG.normal;
}

function sevFg(severity?: string) {
  return SEV_FG[severity ?? "normal"] ?? SEV_FG.normal;
}

function fallbackActions(alert: DashboardAlert): RankedAction[] {
  const owner = alert.initial_owner ?? "";
  const providerCanReceiveSupport = Boolean(alert.provider && alert.provider !== "physical");

  if (alert.confidence < 0.5) {
    return owner === "data-quality"
      ? [
          { key: "monitor", label: "Monitor", weight: 0.72 },
          { key: "data_quality_followup", label: "Follow up with provider feed", weight: 0.55 },
        ]
      : [{ key: "monitor", label: "Monitor", weight: 0.72 }];
  }
  if (owner === "anomaly") {
    return [
      { key: "risk_review", label: "Risk Review", weight: 0.68 },
      { key: "monitor", label: "Monitor", weight: 0.72 },
    ];
  }
  if (owner === "data-quality") {
    return [
      { key: "data_quality_followup", label: "Follow up with provider feed", weight: 0.55 },
      { key: "monitor", label: "Monitor", weight: 0.72 },
    ];
  }
  if (alert.severity === "critical" || alert.severity === "high") {
    return [
      { key: "notify_ops", label: "Notify Operations", weight: 0.97 },
      ...(providerCanReceiveSupport
        ? [{ key: "request_cash_support", label: "Request Provider Liquidity Support", weight: 0.90 }]
        : []),
      { key: "assign_field_officer", label: "Assign Field Officer", weight: 0.94 },
    ];
  }
  return [
    { key: "notify_ops", label: "Notify Operations", weight: 0.97 },
    { key: "monitor", label: "Monitor", weight: 0.72 },
  ];
}

function buildDecisionQueue(data: DashboardSummary): DecisionItem[] {
  const alerts = (data.alerts ?? [])
    .filter(alert => !CLOSED_STATUSES.has(alert.status))
    .slice()
    .sort((left, right) => {
      if (right.priority_score !== left.priority_score) return right.priority_score - left.priority_score;
      return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
    })
    .map((alert): DecisionItem => ({
      key: `alert:${alert.id}`,
      kind: "alert",
      alertId: alert.id,
      severity: alert.severity,
      priorityScore: alert.priority_score,
      provider: alert.provider,
      title: alert.title,
      summary: alert.summary,
      reasons: alert.reasons ?? [],
      confidence: alert.confidence,
      recommended: alert.recommended_actions?.length ? alert.recommended_actions : fallbackActions(alert),
      ownerRole: alert.owner_role,
      ownerLabel: alert.owner_label,
      status: alert.status,
    }));

  if (alerts.length) return alerts;

  // A forecast can become actionable before the next analysis tick creates
  // a case. Surface every pressured provider, but do not pretend a workflow
  // action was recorded until an alert/case exists.
  return (data.providers ?? [])
    .filter(provider => provider.hours_to_shortage != null && provider.hours_to_shortage < 6)
    .slice()
    .sort((left, right) => (left.hours_to_shortage ?? Infinity) - (right.hours_to_shortage ?? Infinity))
    .map((provider): DecisionItem => {
      const hours = provider.hours_to_shortage ?? 6;
      const severity = hours < 0.5 ? "critical" : hours < 2 ? "high" : "low";
      return {
        key: `forecast:${provider.provider}`,
        kind: "forecast",
        severity,
        priorityScore: data.overall_score ?? 0,
        provider: provider.provider,
        title: `${provider.provider.toUpperCase()} projected to deplete soon`,
        summary: `The balance may run out in ${provider.shortage_eta_human ?? "under six hours"}. Run the next analysis tick to create an evidence-backed case before coordinating action.`,
        reasons: provider.forecast_reasons ?? [],
        confidence: provider.forecast_confidence,
        recommended: [],
        ownerRole: "ops",
        ownerLabel: "Provider Operations",
        status: "awaiting analysis",
      };
    });
}

function confidenceLabel(confidence: number) {
  if (confidence >= 75) return "High";
  if (confidence >= 50) return "Moderate";
  return "Low";
}

export function DecisionRecommendationPanel({
  data,
  supportRequests = [],
  onActionTaken,
  onSupportChanged,
}: {
  data: DashboardSummary;
  supportRequests?: CashSupportRequest[];
  onActionTaken?: () => void;
  onSupportChanged?: () => void;
}) {
  const { principal } = usePrincipal();
  const role = principal?.role ?? "agent";
  const decisions = useMemo(() => buildDecisionQueue(data), [data]);
  const decisionSignature = decisions.map(item => item.key).join("|");

  const [selectedKey, setSelectedKey] = useState<string | null>(decisions[0]?.key ?? null);
  const [busy, setBusy] = useState<string | null>(null);
  const [completedActions, setCompletedActions] = useState<Record<string, boolean>>({});
  const [actedDecisions, setActedDecisions] = useState<Record<string, boolean>>({});
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; message: string } | null>(null);

  useEffect(() => {
    setSelectedKey(current => {
      if (current && decisions.some(item => item.key === current)) return current;
      return decisions[0]?.key ?? null;
    });
  }, [decisionSignature]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = decisions.find(item => item.key === selectedKey) ?? decisions[0];
  const activeSupport = selected?.provider
    ? supportRequests.find(request =>
        request.provider === selected.provider && ACTIVE_SUPPORT_STATUSES.has(request.status),
      )
    : undefined;

  function nextDecisionKey(currentKey: string): string | null {
    if (decisions.length < 2) return null;
    const currentIndex = decisions.findIndex(item => item.key === currentKey);
    const nextIndex = currentIndex >= 0 ? (currentIndex + 1) % decisions.length : 0;
    return decisions[nextIndex]?.key ?? null;
  }

  async function fireAction(decision: DecisionItem, actionKey: RecommendedActionKey) {
    if (decision.kind !== "alert" || !decision.alertId) return;
    const taskKey = `${decision.key}:${actionKey}`;
    setBusy(taskKey);
    setFeedback(null);
    try {
      const result = await client.executeRecommendedAction(decision.alertId, actionKey);
      const provider = String(result.cash_support_provider ?? decision.provider ?? "the provider").toUpperCase();
      const amount = result.cash_support_amount == null
        ? null
        : Number(result.cash_support_amount).toLocaleString();
      const nextKey = nextDecisionKey(decision.key);
      const next = decisions.find(item => item.key === nextKey);

      setCompletedActions(current => ({ ...current, [taskKey]: true }));
      setActedDecisions(current => ({ ...current, [decision.key]: true }));
      if (nextKey) setSelectedKey(nextKey);

      const actionMessage = actionKey === "request_cash_support"
        ? result.cash_support_reused
          ? `${provider} already has active support request #${result.cash_support_request_id}; no duplicate was created.`
          : `Created a forecast-sized${amount ? ` ৳${amount}` : ""} support request for ${provider} only.`
        : actionKey === "notify_ops"
          ? "Operations received a durable case notification."
          : "The action was recorded in the case timeline.";
      setFeedback({
        tone: "success",
        message: `${actionMessage}${next ? ` Now showing the next priority: ${String(next.provider ?? "case").toUpperCase()} · ${next.title}` : ""}`,
      });
      onActionTaken?.();
      if (actionKey === "request_cash_support") onSupportChanged?.();
    } catch (error: any) {
      const raw = String(error?.message || error);
      const match = raw.match(/^API\s+\d+\s+[^:]+:\s*(.*)$/);
      const detail = match ? match[1].replace(/^"|"$/g, "") : raw;
      setFeedback({
        tone: "error",
        message: /Illegal transition/i.test(detail)
          ? `This step is not applicable while the case is ${decision.status.replaceAll("_", " ")}. Choose another recommended step.`
          : detail,
      });
    } finally {
      setBusy(null);
    }
  }

  if (!selected) {
    return (
      <Card style={{ marginTop: 16, borderColor: "#bbf7d0" }}>
        <div style={{ fontSize: 12, color: "#166534", textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>Decision support</div>
        <div style={{ fontSize: 21, fontWeight: 750, color: "#166534", marginTop: 6 }}>No action needed</div>
        <div style={{ color: "#475569", marginTop: 4 }}>No provider currently has an actionable liquidity or unusual-activity case.</div>
      </Card>
    );
  }

  const conf = Math.round(selected.confidence * 100);
  const providerCount = new Set(decisions.map(item => item.provider).filter(Boolean)).size;

  return (
    <Card style={{ marginTop: 16, borderColor: selected.severity === "critical" ? "#fecaca" : "#cbd5e1", padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontSize: 12, color: "#64748b", letterSpacing: 1, textTransform: "uppercase", fontWeight: 700 }}>Decision support · priority queue</div>
          <div style={{ fontSize: 21, fontWeight: 750, marginTop: 5 }}>
            {decisions.length} {decisions.length === 1 ? "active decision" : "active decisions"} across {providerCount} {providerCount === 1 ? "provider" : "providers"}
          </div>
          <div style={{ color: "#64748b", fontSize: 13, marginTop: 3 }}>
            Highest priority is selected first. A successful action advances to the next case; completed cases remain available for their other steps.
          </div>
        </div>
        <span style={{ background: "#eff6ff", color: "#1d4ed8", borderRadius: 999, padding: "6px 10px", fontSize: 12, fontWeight: 750 }}>
          {decisions.filter(item => actedDecisions[item.key]).length}/{decisions.length} cases acted on
        </span>
      </div>

      {feedback && (
        <div style={{
          marginTop: 12,
          color: feedback.tone === "success" ? "#166534" : "#b91c1c",
          background: feedback.tone === "success" ? "#f0fdf4" : "#fef2f2",
          border: `1px solid ${feedback.tone === "success" ? "#bbf7d0" : "#fecaca"}`,
          padding: "9px 11px",
          borderRadius: 8,
          fontSize: 13,
        }}>
          {feedback.tone === "success" ? "✓ " : ""}{feedback.message}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "minmax(250px, 0.7fr) minmax(0, 2fr)", gap: 16, marginTop: 16 }}>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 10, padding: 10, alignSelf: "start" }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", fontWeight: 750, padding: "2px 3px 8px" }}>Cases in priority order</div>
          <div style={{ display: "grid", gap: 8 }}>
            {decisions.map((item, index) => {
              const isSelected = item.key === selected.key;
              return (
                <button
                  key={item.key}
                  type="button"
                  aria-pressed={isSelected}
                  onClick={() => setSelectedKey(item.key)}
                  style={{
                    textAlign: "left",
                    width: "100%",
                    background: isSelected ? "#eff6ff" : "#fff",
                    border: `1px solid ${isSelected ? "#60a5fa" : "#e2e8f0"}`,
                    borderRadius: 9,
                    padding: 10,
                    cursor: "pointer",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ color: "#64748b", fontSize: 11, fontWeight: 750 }}>#{index + 1} · PRIORITY {item.priorityScore}</span>
                    {actedDecisions[item.key] && <span style={{ color: "#166534", fontSize: 11, fontWeight: 750 }}>✓ ACTED</span>}
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 5 }}>
                    <span style={{ background: sevBg(item.severity), color: sevFg(item.severity), padding: "2px 6px", borderRadius: 999, fontSize: 10, fontWeight: 800 }}>{item.severity.toUpperCase()}</span>
                    <b style={{ fontSize: 12 }}>{String(item.provider ?? "shared").toUpperCase()}</b>
                    {item.alertId && <span style={{ color: "#94a3b8", fontSize: 11 }}>Alert #{item.alertId}</span>}
                  </div>
                  <div style={{ fontSize: 13, fontWeight: 700, marginTop: 5, lineHeight: 1.25 }}>{item.title}</div>
                  <div style={{ color: "#64748b", fontSize: 11, marginTop: 4 }}>{item.recommended.length} coordinated {item.recommended.length === 1 ? "step" : "steps"}</div>
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", gap: 9, alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ background: sevBg(selected.severity), color: sevFg(selected.severity), padding: "4px 10px", borderRadius: 999, fontSize: 12, fontWeight: 800 }}>{selected.severity.toUpperCase()}</span>
            <div style={{ fontSize: 20, fontWeight: 750 }}>{selected.title}</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(135px, 1fr))", gap: 9, marginTop: 12 }}>
            {[
              ["Provider target", String(selected.provider ?? "—").toUpperCase()],
              ["Responsible owner", selected.ownerLabel],
              ["Case status", selected.status.replaceAll("_", " ")],
              ["Confidence", `${confidenceLabel(conf)} · ${conf}%`],
              ["Priority", `${selected.priorityScore}/100`],
            ].map(([label, value]) => (
              <div key={label} style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8, padding: "9px 10px" }}>
                <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 }}>{label}</div>
                <div style={{ fontSize: 12, fontWeight: 700, marginTop: 3 }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12, padding: "10px 12px", background: conf < 50 ? "#fff7ed" : "#f8fafc", borderLeft: `3px solid ${conf < 50 ? "#f59e0b" : "#64748b"}` }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: "#475569", textTransform: "uppercase" }}>Evidence-based rationale</div>
            <div style={{ fontSize: 13, color: "#334155", marginTop: 4 }}>{selected.summary}</div>
            {selected.reasons.slice(0, 3).map((reason, index) => <div key={index} style={{ fontSize: 12, color: "#64748b", marginTop: 3 }}>• {reason}</div>)}
            {conf < 50 && <div style={{ fontSize: 12, color: "#9a3412", marginTop: 5, fontWeight: 650 }}>Low confidence: verify the current balance and evidence before escalation.</div>}
          </div>

          <div style={{ marginTop: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <div style={{ fontSize: 12, color: "#475569", textTransform: "uppercase", letterSpacing: 0.6, fontWeight: 800 }}>All coordinated next steps</div>
              {selected.provider && <div style={{ fontSize: 12, color: "#9a3412", fontWeight: 700 }}>Provider-scoped: {selected.provider.toUpperCase()} only</div>}
            </div>

            {selected.kind === "forecast" ? (
              <div style={{ border: "1px dashed #94a3b8", borderRadius: 9, padding: 13, marginTop: 8, color: "#475569", fontSize: 13 }}>
                This is a forecast watch, not an open case. Run the analysis tick; executable actions appear only after the platform creates an evidence-backed alert and case.
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 9, marginTop: 8 }}>
                {selected.recommended.map((action, index) => {
                  const meta = ACTION_STYLE[action.key] ?? {
                    label: action.label,
                    color: "#64748b",
                    hint: "Record this recommended case step.",
                    icon: "•",
                    owner: selected.ownerLabel,
                  };
                  const taskKey = `${selected.key}:${action.key}`;
                  const isBusy = busy === taskKey;
                  const isCompleted = Boolean(completedActions[taskKey]);
                  const isSupportAction = action.key === "request_cash_support";
                  const hasActiveSupport = isSupportAction && Boolean(activeSupport);
                  const allowed = canPerformRecommendedAction(role, action.key);
                  const disabled = Boolean(busy) || !allowed || isCompleted || hasActiveSupport;
                  const actionLabel = isSupportAction && selected.provider
                    ? `Request ${selected.provider.toUpperCase()} Support`
                    : meta.label;

                  return (
                    <div key={action.key} style={{ border: `1px solid ${isCompleted || hasActiveSupport ? "#86efac" : "#e2e8f0"}`, background: isCompleted || hasActiveSupport ? "#f0fdf4" : "#fff", borderRadius: 9, padding: 12 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                        <span style={{ fontSize: 11, color: "#64748b", fontWeight: 750 }}>STEP {index + 1}</span>
                        <span style={{ fontSize: 10, color: allowed ? meta.color : "#64748b", background: "#f8fafc", borderRadius: 999, padding: "3px 6px", fontWeight: 700 }}>{allowed ? "YOUR ACTION" : meta.owner.toUpperCase()}</span>
                      </div>
                      <div style={{ fontSize: 14, fontWeight: 750, marginTop: 6 }}>{meta.icon} {meta.label}</div>
                      <div style={{ fontSize: 12, color: "#64748b", marginTop: 4, minHeight: 32 }}>{meta.hint}</div>
                      {isSupportAction && selected.provider && (
                        <div style={{ fontSize: 11, color: "#9a3412", marginTop: 5, fontWeight: 700 }}>Target: {selected.provider.toUpperCase()} · separate provider balance</div>
                      )}
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => fireAction(selected, action.key as RecommendedActionKey)}
                        title={!allowed ? `Assigned to ${meta.owner}` : meta.hint}
                        style={{
                          marginTop: 9,
                          width: "100%",
                          background: disabled ? "#e2e8f0" : meta.color,
                          color: disabled ? "#64748b" : "#fff",
                          border: 0,
                          borderRadius: 7,
                          padding: "8px 10px",
                          fontSize: 12,
                          fontWeight: 750,
                          cursor: disabled ? "not-allowed" : "pointer",
                        }}
                      >
                        {isBusy
                          ? "Working…"
                          : isCompleted
                            ? "✓ Recorded"
                            : hasActiveSupport
                              ? `Request #${activeSupport?.id} · ${activeSupport?.status}`
                              : allowed
                                ? actionLabel
                                : `Assigned to ${meta.owner}`}
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 12 }}>
        Advisory workflow: actions are role-authorized, provider-scoped, and recorded. The platform never moves funds or makes a final wrongdoing determination.
      </div>
    </Card>
  );
}
