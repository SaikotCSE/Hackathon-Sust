"use client";
import React, { useState } from "react";
import type { DashboardAlert, DashboardProvider, DashboardSummary } from "../lib/types";
import { Card } from "./Primitives";
import { client } from "../lib/client";
import { usePrincipal } from "./PrincipalProvider";
import { canPerformRecommendedAction } from "../lib/rbac";
import type { RecommendedActionKey } from "../lib/client";

const CLOSED_STATUSES = new Set<DashboardAlert["status"]>(["resolved", "closed"]);

// Action key → label, color, hint. Mirrors the catalog in config/decision-weights.json.
const ACTION_STYLE: Record<string, { label: string; color: string; hint: string; icon: string }> = {
  notify_ops:               { label: "Notify Operations",            color: "#0ea5e9", hint: "Hand the case to Provider Operations for triage.", icon: "📣" },
  assign_field_officer:     { label: "Assign Field Officer",         color: "#16a34a", hint: "Dispatch a field officer to top up or verify stock.", icon: "🚚" },
  request_cash_support:     { label: "Request Cash Support",         color: "#ea580c", hint: "Open a cash-support ticket with the provider.", icon: "💵" },
  monitor:                  { label: "Monitor",                       color: "#64748b", hint: "Keep watching — no immediate action.", icon: "👀" },
  risk_review:              { label: "Escalate to Risk Review",      color: "#7c3aed", hint: "Requests advisory evidence review and further-investigation guidance.", icon: "⚖️" },
  data_quality_followup:    { label: "Follow up with provider feed",color: "#0891b2", hint: "Provider feed is degraded — chase the integration team.", icon: "🔌" },
};

const SEV_BG: Record<string, string> = {
  critical: "#fee2e2",
  high:     "#ffedd5",
  low:      "#fef9c3",
  normal:   "#dcfce7",
};
const SEV_FG: Record<string, string> = {
  critical: "#991b1b",
  high:     "#9a3412",
  low:      "#854d0e",
  normal:   "#166534",
};

function sevBg(s?: string) { return SEV_BG[s ?? "normal"] ?? SEV_BG.normal; }
function sevFg(s?: string) { return SEV_FG[s ?? "normal"] ?? SEV_FG.normal; }

/**
 * Pick the single top fused recommendation for the dashboard.
 * Priority: highest-priority non-resolved alert → fallback to the worst
 * provider health (liquidity-only signal even with no alert) → all-clear.
 */
function pickTopRecommendation(d: DashboardSummary): {
  kind: "alert" | "liquidity" | "clear";
  alertId?: number;
  severity?: string;
  priorityScore?: number;
  provider?: string | null;
  title?: string;
  summary?: string;
  reasons?: string[];
  confidence?: number;
  recommended?: Array<{ key: string; label: string; weight: number }>;
  ownerRole?: string;
  ownerLabel?: string;
  fusedExplanation?: string;
  status?: string;
  evidence?: Array<{ source: string; rule?: string; text: string }>;
} {
  const openAlerts = (d.alerts ?? []).filter(a => !CLOSED_STATUSES.has(a.status));
  const top = openAlerts.slice().sort((a, b) => b.priority_score - a.priority_score)[0];

  if (top) {
    return {
      kind: "alert",
      alertId: top.id,
      severity: top.severity,
      priorityScore: top.priority_score,
      provider: top.provider,
      title: top.title,
      summary: top.summary,
      confidence: top.confidence,
      ownerRole: top.owner_role,
      ownerLabel: top.owner_label,
      reasons: top.reasons,
      evidence: top.evidence,
      recommended: top.recommended_actions,
      fusedExplanation: top.fused_explanation,
      status: top.status,
    };
  }

  // No alerts — fall back to the worst projected pressure on any provider.
  const providers = (d.providers ?? []).filter(p => p.provider !== "physical");
  const physical  = (d.providers ?? []).find(p => p.provider === "physical");
  const allRows: DashboardProvider[] = [...providers, ...(physical ? [physical] : [])];
  const worst = allRows
    .filter((p): p is DashboardProvider => p.hours_to_shortage != null)
    .sort((a, b) => (a.hours_to_shortage as number) - (b.hours_to_shortage as number))[0];

  if (worst) {
    const hrs = worst.hours_to_shortage as number;
    if (hrs < 6) {
      const sev = hrs < 0.5 ? "critical" : hrs < 2 ? "high" : "low";
      return {
        kind: "liquidity",
        severity: sev,
        provider: worst.provider,
        title: `${(worst.provider || "").toUpperCase()} projected to deplete soon`,
        summary: `No open alert yet — but the burn-rate forecast shows the balance may run out in ${worst.shortage_eta_human ?? "—"}.`,
        confidence: worst.forecast_confidence,
      };
    }
  }

  return { kind: "clear" };
}

/**
 * Map a top-level recommendation onto ranked suggested actions,
 * using the same selection rules as the orchestrator (so the UI and
 * the server stay in sync).
 */
function deriveActions(rec: ReturnType<typeof pickTopRecommendation>): Array<{ key: string; label: string; weight: number }> {
  if (rec.kind === "alert") {
    // Server already computed these — but we don't have them on the dashboard
    // summary (only on the AlertDetail), so reconstruct from severity + provider.
    const sev = rec.severity ?? "low";
    const isAnomaly = (rec.title ?? "").toLowerCase().includes("unusual")
                   || (rec.summary ?? "").toLowerCase().includes("anomaly");
    const isDq = (rec.summary ?? "").toLowerCase().includes("feed quality");
    const out: Array<{ key: string; label: string; weight: number }> = [];

    if (sev === "critical" || sev === "high") {
      out.push({ key: "notify_ops",            label: "Notify Operations",       weight: 0.97 });
      out.push({ key: "assign_field_officer",  label: "Assign Field Officer",    weight: 0.94 });
      out.push({ key: "request_cash_support",  label: "Request Cash Support",    weight: 0.90 });
    } else if (sev === "low") {
      out.push({ key: "notify_ops", label: "Notify Operations", weight: 0.97 });
      out.push({ key: "monitor",    label: "Monitor",            weight: 0.72 });
    } else {
      out.push({ key: "monitor", label: "Monitor", weight: 0.72 });
    }
    if (isAnomaly) {
      const filtered = out.filter(a => a.key !== "monitor");
      filtered.push({ key: "risk_review", label: "Escalate to Risk Review", weight: 0.68 });
      return filtered;
    }
    if (isDq) {
      const filtered = out.filter(a => a.key !== "request_cash_support");
      filtered.push({ key: "data_quality_followup", label: "Follow up with provider feed", weight: 0.55 });
      return filtered;
    }
    return out;
  }

  if (rec.kind === "liquidity") {
    const sev = rec.severity ?? "low";
    if (sev === "critical" || sev === "high") {
      return [
        { key: "notify_ops",           label: "Notify Operations",       weight: 0.97 },
        { key: "assign_field_officer", label: "Assign Field Officer",    weight: 0.94 },
        { key: "request_cash_support", label: "Request Cash Support",    weight: 0.90 },
      ];
    }
    return [
      { key: "notify_ops", label: "Notify Operations", weight: 0.97 },
      { key: "monitor",    label: "Monitor",           weight: 0.72 },
    ];
  }

  return [{ key: "monitor", label: "Monitor", weight: 0.72 }];
}

function deriveOwner(rec: ReturnType<typeof pickTopRecommendation>): { role: string; label: string } {
  if (rec.kind === "alert") {
    return { role: rec.ownerRole ?? "ops", label: rec.ownerLabel ?? "Provider Operations" };
  }
  if (rec.kind === "liquidity") {
    if (rec.severity === "critical") return { role: "ops", label: "Provider Operations / Network Coordination" };
    return { role: "ops", label: "Provider Operations" };
  }
  return { role: "—", label: "—" };
}

export function DecisionRecommendationPanel({
  data,
  onActionTaken,
}: {
  data: DashboardSummary;
  onActionTaken?: () => void;
}) {
  const { principal } = usePrincipal();
  const role = (principal?.role ?? "agent") as string;
  const rec = pickTopRecommendation(data);
  const actions = rec.kind === "alert" && rec.recommended?.length ? rec.recommended : deriveActions(rec);
  const owner = deriveOwner(rec);
  const conf = Math.round((rec.confidence ?? 0.95) * 100);

  const actionable = actions.filter(a => canPerformRecommendedAction(role, a.key));
  const primaryAction = actionable[0] ?? null;
  const secondaryActions = actions.filter(a => a.key !== primaryAction?.key);

  const [busy, setBusy] = useState<RecommendedActionKey | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionOk, setActionOk] = useState<RecommendedActionKey | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  async function fireAction(actionKey: RecommendedActionKey) {
    if (rec.kind !== "alert" || !rec.alertId) return;
    setBusy(actionKey);
    setActionError(null);
    setActionOk(null);
    setActionMessage(null);
    try {
      const result: any = await client.executeRecommendedAction(rec.alertId, actionKey);
      setActionOk(actionKey);
      setActionMessage(
        actionKey === "request_cash_support" && result.cash_support_amount
          ? `Sent a forecast-sized ৳${Number(result.cash_support_amount).toLocaleString()} request to ${String(rec.provider ?? "the provider").toUpperCase()}.`
          : actionKey === "notify_ops"
            ? "Operations received a durable case notification."
            : "Action recorded in the case timeline."
      );
      onActionTaken?.();
    } catch (e: any) {
      // The client throws raw `API <status> <path>: <body>` strings. Strip
      // that prefix and turn the server's plain HTTPException detail into a
      // human-friendly sentence — the panel surfaces recommendations, not
      // raw stack traces.
      const raw = String(e?.message || e);
      const m = raw.match(/^API\s+\d+\s+[^:]+:\s*(.*)$/);
      const detail = m ? m[1].replace(/^"|"$/g, "") : raw;
      const friendly =
        /Illegal transition/i.test(detail)
          ? `This action isn't applicable to the case in its current state (${rec.kind === "alert" ? "see case state" : "no alert"}). The case may have already moved on — try another recommendation.`
          : detail;
      setActionError(friendly);
    } finally {
      setBusy(null);
    }
  }

  if (rec.kind === "clear") {
    return <Card style={{ marginTop: 16, borderColor: "#bbf7d0" }}>
      <div style={{ fontSize: 12, color: "#166534", textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>Decision support</div>
      <div style={{ fontSize: 21, fontWeight: 750, color: "#166534", marginTop: 6 }}>No action needed</div>
      <div style={{ color: "#475569", marginTop: 4 }}>No provider currently has an actionable liquidity or unusual-activity alert.</div>
    </Card>;
  }

  const confidenceLabel = conf >= 75 ? "High" : conf >= 50 ? "Moderate" : "Low";
  const primaryMeta = primaryAction ? (ACTION_STYLE[primaryAction.key] ?? { label: primaryAction.label, color: "#475569", hint: "", icon: "•" }) : null;

  return (
    <Card style={{ marginTop: 16, borderColor: rec.severity === "critical" ? "#fecaca" : "#cbd5e1", padding: 20 }}>
      <div style={{ fontSize: 12, color: "#64748b", letterSpacing: 1, textTransform: "uppercase", fontWeight: 700 }}>Decision support</div>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 7 }}>
        <span style={{ background: sevBg(rec.severity), color: sevFg(rec.severity), padding: "4px 10px", borderRadius: 999, fontSize: 12, fontWeight: 800 }}>{(rec.severity ?? "low").toUpperCase()}</span>
        <div style={{ fontSize: 21, fontWeight: 750 }}>{rec.title}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10, marginTop: 14 }}>
        {[
          ["Provider", String(rec.provider ?? "—").toUpperCase()],
          ["Owner", owner.label],
          ["Case status", String(rec.status ?? "awaiting case").replaceAll("_", " ")],
          ["Confidence", `${confidenceLabel} · ${conf}%`],
          ["Priority", `${rec.priorityScore ?? data.overall_score ?? 0}/100`],
        ].map(([label, value]) => <div key={label} style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8, padding: "9px 11px" }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: .6 }}>{label}</div>
          <div style={{ fontSize: 13, fontWeight: 700, marginTop: 3 }}>{value}</div>
        </div>)}
      </div>

      <div style={{ marginTop: 14, padding: "11px 13px", background: conf < 50 ? "#fff7ed" : "#f8fafc", borderLeft: `3px solid ${conf < 50 ? "#f59e0b" : "#64748b"}` }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: "#475569", textTransform: "uppercase" }}>Why this recommendation</div>
        <div style={{ fontSize: 14, color: "#334155", marginTop: 4 }}>{rec.summary}</div>
        {(rec.reasons ?? []).slice(0, 2).map((reason, i) => <div key={i} style={{ fontSize: 12, color: "#64748b", marginTop: 3 }}>• {reason}</div>)}
        {conf < 50 && <div style={{ fontSize: 12, color: "#9a3412", marginTop: 5, fontWeight: 650 }}>Low confidence: verify current balance and evidence before escalating.</div>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14, marginTop: 14 }}>
        <div style={{ border: `1px solid ${primaryMeta?.color ?? "#cbd5e1"}`, borderRadius: 9, padding: 14 }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", fontWeight: 700 }}>Recommended for you</div>
          {primaryAction && primaryMeta ? <>
            <div style={{ fontSize: 16, fontWeight: 750, marginTop: 5 }}>{primaryMeta.icon} {primaryMeta.label}</div>
            <div style={{ fontSize: 13, color: "#475569", marginTop: 3 }}>{primaryMeta.hint}</div>
            <button onClick={() => fireAction(primaryAction.key as RecommendedActionKey)} disabled={busy === primaryAction.key || rec.kind !== "alert"} style={{ marginTop: 10, background: primaryMeta.color, color: "#fff", border: 0, borderRadius: 7, padding: "8px 14px", fontWeight: 700, cursor: "pointer" }}>
              {busy === primaryAction.key ? "Working…" : actionOk === primaryAction.key ? "✓ Completed" : primaryMeta.label}
            </button>
          </> : <div style={{ color: "#64748b", marginTop: 6 }}>No action is assigned to your role. The named owner will receive it in their inbox.</div>}
        </div>
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 9, padding: 14 }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", fontWeight: 700 }}>Other coordinated steps</div>
          <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 8 }}>
            {secondaryActions.map(a => {
              const meta = ACTION_STYLE[a.key] ?? { label: a.label, color: "#64748b", hint: "", icon: "•" };
              const allowed = canPerformRecommendedAction(role, a.key) && rec.kind === "alert";
              return <button key={a.key} disabled={!allowed || busy === a.key} onClick={() => fireAction(a.key as RecommendedActionKey)} title={allowed ? meta.hint : `Assigned to another stakeholder`} style={{ background: allowed ? "#fff" : "#f1f5f9", color: allowed ? meta.color : "#94a3b8", border: `1px solid ${allowed ? meta.color : "#cbd5e1"}`, borderRadius: 7, padding: "6px 9px", fontSize: 12, fontWeight: 650, cursor: allowed ? "pointer" : "not-allowed" }}>{meta.label}</button>;
            })}
          </div>
        </div>
      </div>
      {actionMessage && <div style={{ marginTop: 10, color: "#166534", background: "#f0fdf4", padding: "8px 10px", borderRadius: 7, fontSize: 13 }}>✓ {actionMessage}</div>}
      {actionError && <div style={{ marginTop: 10, color: "#b91c1c", background: "#fef2f2", padding: "8px 10px", borderRadius: 7, fontSize: 13 }}>{actionError}</div>}
      <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 10 }}>Advisory workflow: every action is role-authorized and recorded. Nothing happens automatically.</div>
    </Card>
  );
}
