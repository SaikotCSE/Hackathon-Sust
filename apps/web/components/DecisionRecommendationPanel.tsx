"use client";
import React, { useMemo, useState } from "react";
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
  risk_review:              { label: "Escalate to Risk Review",      color: "#7c3aed", hint: "Hands off to Risk / Compliance for final ruling.", icon: "⚖️" },
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
    if (rec.severity === "critical") return { role: "ops", label: "Provider Operations (area-manager tier)" };
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
  const actions = deriveActions(rec);
  const owner = deriveOwner(rec);
  const conf = Math.round((rec.confidence ?? 0.95) * 100);

  // Filter the recommended actions by the principal's role. The full list
  // is still computed (so we can show the "you can't fire this" hint for
  // actions restricted to ops/risk), but only role-allowed actions get a
  // real button.
  const allowedActionKeys = useMemo(
    () => new Set(actions.filter(a => canPerformRecommendedAction(role, a.key)).map(a => a.key)),
    [actions, role],
  );

  const [busy, setBusy] = useState<RecommendedActionKey | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionOk, setActionOk] = useState<RecommendedActionKey | null>(null);

  async function fireAction(actionKey: RecommendedActionKey) {
    if (rec.kind !== "alert" || !rec.alertId) return;
    setBusy(actionKey);
    setActionError(null);
    setActionOk(null);
    try {
      await client.executeRecommendedAction(rec.alertId, actionKey);
      setActionOk(actionKey);
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

  // Count backup signals for the "why this is the top recommendation" line.
  const openAlerts = (data.alerts ?? []).filter(a => !CLOSED_STATUSES.has(a.status));
  const criticalProviders = (data.providers ?? []).filter(
    p => p.health === "critical" || p.health === "high"
  );

  return (
    <Card style={{ marginTop: 16, borderColor: rec.kind === "clear" ? "#bbf7d0" : "#cbd5e1" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, color: "#64748b", letterSpacing: 1, textTransform: "uppercase", fontWeight: 600 }}>
            Decision Intelligence — What should I do next?
          </div>

          {rec.kind === "clear" ? (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 22, fontWeight: 700, color: "#166534" }}>
                All clear. No action needed.
              </div>
              <div style={{ fontSize: 15, color: "#475569", marginTop: 6 }}>
                Every provider is within its healthy range. Continue routine monitoring.
              </div>
            </div>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                <span style={{
                  background: sevBg(rec.severity), color: sevFg(rec.severity),
                  padding: "4px 12px", borderRadius: 999, fontSize: 13, fontWeight: 700,
                }}>
                  {(rec.severity ?? "low").toUpperCase()}
                </span>
                <div style={{ fontSize: 21, fontWeight: 700, color: "#0f172a" }}>
                  {rec.title ?? "Take action"}
                </div>
                {rec.provider && (
                  <span style={{ fontSize: 14, color: "#64748b" }}>
                    · provider <b>{String(rec.provider).toUpperCase()}</b>
                  </span>
                )}
                {rec.priorityScore != null && (
                  <span style={{ fontSize: 14, color: "#64748b" }}>
                    · priority <b>{rec.priorityScore}/100</b>
                  </span>
                )}
                {rec.confidence != null && (
                  <span style={{ fontSize: 14, color: "#64748b" }}>
                    · confidence <b>{conf}%</b>
                  </span>
                )}
              </div>

              {rec.summary && (
                <div style={{ fontSize: 15, color: "#475569", marginTop: 8, lineHeight: 1.5 }}>
                  {rec.summary}
                </div>
              )}

              <div style={{ fontSize: 14, color: "#64748b", marginTop: 10 }}>
                Owner: <b style={{ color: "#0f172a" }}>{owner.label}</b>
                <span style={{ color: "#94a3b8" }}> · role: {owner.role}</span>
              </div>
            </>
          )}
        </div>

        <div style={{ textAlign: "right", minWidth: 160 }}>
          <div style={{ fontSize: 12, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 1, fontWeight: 600 }}>
            Signals feeding this decision
          </div>
          <div style={{ fontSize: 14, color: "#0f172a", marginTop: 6, fontVariantNumeric: "tabular-nums" }}>
            <b style={{ color: openAlerts.length ? "#dc2626" : "#16a34a" }}>{openAlerts.length}</b>{" "}
            <span style={{ color: "#64748b" }}>open alerts</span>
          </div>
          <div style={{ fontSize: 14, color: "#0f172a", fontVariantNumeric: "tabular-nums" }}>
            <b style={{ color: criticalProviders.length ? "#dc2626" : "#16a34a" }}>{criticalProviders.length}</b>{" "}
            <span style={{ color: "#64748b" }}>providers under pressure</span>
          </div>
          <div style={{ fontSize: 14, color: "#0f172a", fontVariantNumeric: "tabular-nums" }}>
            overall pressure: <b>{data.overall_score ?? 0}/100</b>
          </div>
        </div>
      </div>

      {/* Ranked actions */}
      <div style={{ marginTop: 18 }}>
        <div style={{
          fontSize: 12, color: "#64748b",
          textTransform: "uppercase", letterSpacing: 1, fontWeight: 600,
        }}>
          Recommended next moves (by weight)
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 10 }}>
          {actions.map((a, i) => {
            const meta = ACTION_STYLE[a.key] ?? {
              label: a.label, color: "#475569", hint: "", icon: "•",
            };
            const isTop = i === 0;
            const allowed = allowedActionKeys.has(a.key);
            const canFire = allowed && rec.kind === "alert" && !!rec.alertId;
            const justFired = actionOk === a.key;
            const isBusy = busy === a.key;
            const ownerForAction = roleOwnerFor(a.key);
            return (
              <div key={a.key} style={{
                background: isTop ? "#f8fafc" : "#fff",
                border: `1px solid ${isTop ? meta.color : "#e5e7eb"}`,
                borderLeft: `4px solid ${meta.color}`,
                borderRadius: 8,
                padding: "12px 14px",
                position: "relative",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 16 }}>{meta.icon}</span>
                  <div style={{ fontWeight: 700, fontSize: 15, color: "#0f172a", flex: 1 }}>
                    {meta.label}
                  </div>
                  <span style={{
                    background: meta.color, color: "#fff",
                    padding: "2px 9px", borderRadius: 999,
                    fontSize: 12, fontWeight: 700,
                  }}>
                    w {a.weight.toFixed(2)}
                  </span>
                </div>
                <div style={{ fontSize: 14, color: "#475569", marginTop: 6, lineHeight: 1.45 }}>
                  {meta.hint}
                </div>

                {/* Inline action button — only fires if the principal's role
                    is allowed AND we have an Alert to act on. For restricted
                    actions we surface a "why" hint so the user understands
                    the recommendation exists but isn't theirs to take. */}
                <div style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <button
                    onClick={() => fireAction(a.key as RecommendedActionKey)}
                    disabled={!canFire || isBusy}
                    title={
                      canFire
                        ? meta.hint
                        : rec.kind !== "alert"
                          ? "No open alert to act on."
                          : `${role} cannot ${meta.label.toLowerCase()}${ownerForAction ? ` — restricted to ${ownerForAction}` : ""}`
                    }
                    style={{
                      background: canFire ? meta.color : "#e5e7eb",
                      color: canFire ? "#fff" : "#94a3b8",
                      border: 0, borderRadius: 6,
                      padding: "6px 12px", fontSize: 13, fontWeight: 600,
                      cursor: canFire ? "pointer" : "not-allowed",
                      opacity: isBusy ? 0.7 : 1,
                    }}
                  >
                    {isBusy
                      ? "Recording…"
                      : justFired
                        ? "✓ Logged"
                        : canFire
                          ? `Take action: ${meta.label}`
                          : `${meta.label} · restricted`}
                  </button>
                  {!allowed && (
                    <span style={{ fontSize: 12, color: "#94a3b8" }}>
                      {ownerForAction
                        ? `Only ${ownerForAction} can take this action.`
                        : `Your role (${role}) cannot take this action.`}
                    </span>
                  )}
                </div>

                {isTop && (
                  <div style={{
                    position: "absolute", top: -8, right: 12,
                    background: meta.color, color: "#fff",
                    fontSize: 10, fontWeight: 700, letterSpacing: 1,
                    padding: "2px 7px", borderRadius: 999,
                  }}>
                    TOP
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {actionError && (
          <div style={{ marginTop: 10, color: "#dc2626", fontSize: 13 }}>
            {actionError}
          </div>
        )}
        <div style={{ fontSize: 13, color: "#94a3b8", marginTop: 10, fontStyle: "italic" }}>
          We surface recommendations — humans decide. No transactions are executed automatically.
        </div>
      </div>
    </Card>
  );
}

// Maps each recommended action key to the role(s) authorized to take it, so
// the panel can show "Only Provider Operations can take this action" hints
// to users whose role is restricted. Mirrors apps/web/lib/rbac.ts.
function roleOwnerFor(actionKey: string): string {
  switch (actionKey) {
    case "notify_ops":
      return "Agent / Ops / Risk";
    case "assign_field_officer":
      return "Provider Operations / Network Coordination";
    case "request_cash_support":
      return "Provider Operations / Network Coordination";
    case "risk_review":
      return "Agent / Ops / Risk";
    case "data_quality_followup":
      return "Provider / Ops / Risk";
    case "monitor":
      return "";
    default:
      return "";
  }
}