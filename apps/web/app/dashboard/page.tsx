"use client";
import React, { useEffect, useState } from "react";
import useSWR from "swr";
import { client, type ScenarioKind } from "../../lib/client";
import { Card, Disclaimer, PageHeader } from "../../components/Primitives";
import { AlertCard } from "../../components/AlertCard";
import { ProviderLiquidityCard } from "../../components/ProviderLiquidityCard";
import { ProviderChartCard } from "../../components/ProviderChartCard";
import { DecisionRecommendationPanel } from "../../components/DecisionRecommendationPanel";
import { AlertActionStrip } from "../../components/AlertActionStrip";
import { usePrincipal } from "../../components/PrincipalProvider";
import { can } from "../../lib/rbac";
import type { DashboardSummary, OperationalLiquiditySummary, DashboardProvider, CashSupportRequest } from "../../lib/types";

// One-time inline keyframes for the "Loading X view…" pill at the bottom
// of the dashboard. Kept here (not globals.css) so this file's behavior is
// self-contained when diffed.
const SA_PULSE_KEYFRAMES = `
@keyframes saPulse { 0%,100% { opacity: 0.4; transform: scale(0.85); } 50% { opacity: 1; transform: scale(1); } }
`;

if (typeof document !== "undefined" && !document.getElementById("sa-pulse-keyframes")) {
  const style = document.createElement("style");
  style.id = "sa-pulse-keyframes";
  style.textContent = SA_PULSE_KEYFRAMES;
  document.head.appendChild(style);
}

// Read `?agent_id=` from the URL on the client. The agent view must
// refetch when this changes (e.g. an ops user clicking through to an
// agent's detail page). On the server `window` is undefined so we fall
// back to 1 — same default the API uses.
function readAgentIdFromUrl(): number {
  if (typeof window === "undefined") return 1;
  const raw = new URLSearchParams(window.location.search).get("agent_id");
  const n = raw ? Number(raw) : NaN;
  return Number.isFinite(n) && n > 0 ? n : 1;
}

type DemoScenario = {
  kind: ScenarioKind;
  category: string;
  title: string;
  provider: "bkash" | "nagad" | "rocket";
  description: string;
  expected: string;
  intendedSeverity: "normal" | "low" | "high" | "critical";
  isAnomaly: boolean;
  accent: string;
  tint: string;
};

type ScenarioFeedback = {
  tone: "success" | "error";
  title: string;
  detail: string;
} | null;

const SCENARIOS: readonly DemoScenario[] = [
  {
    kind: "bkash_surge",
    category: "Liquidity pressure",
    title: "bKash e-money drawdown",
    provider: "bkash",
    description: "Simulates sustained customer demand that rapidly reduces the outlet's separate bKash position.",
    expected: "Forecast an approximate shortage time and route a safe Operations response.",
    intendedSeverity: "critical",
    isAnomaly: false,
    accent: "#dc2626",
    tint: "#fff1f2",
  },
  {
    kind: "repeated_amount",
    category: "Unusual behavior",
    title: "Repeated Nagad amounts",
    provider: "nagad",
    description: "Creates several near-identical transactions from synthetic counterparties in a short window.",
    expected: "Show record-level evidence, uncertainty, and a human-review recommendation.",
    intendedSeverity: "high",
    isAnomaly: true,
    accent: "#7c3aed",
    tint: "#f5f3ff",
  },
  {
    kind: "structuring",
    category: "Pattern requiring review",
    title: "Amounts near ৳5,000",
    provider: "bkash",
    description: "Creates a clustered threshold pattern without asserting intent or wrongdoing.",
    expected: "Explain why the pattern was flagged and why a benign explanation remains possible.",
    intendedSeverity: "high",
    isAnomaly: true,
    accent: "#9333ea",
    tint: "#faf5ff",
  },
  {
    kind: "rocket_delay",
    category: "Data-quality fallback",
    title: "Delayed Rocket feed",
    provider: "rocket",
    description: "Marks the Rocket provider feed as late before the next analytical cycle.",
    expected: "Pause unsupported projections, lower confidence, and identify the feed owner.",
    intendedSeverity: "low",
    isAnomaly: false,
    accent: "#0891b2",
    tint: "#ecfeff",
  },
  {
    kind: "salary_day",
    category: "Legitimate-demand control",
    title: "Nagad salary-day volume",
    provider: "nagad",
    description: "Creates diverse high-volume activity with an observed salary-calendar context.",
    expected: "Avoid elevating compatible volume alone; genuine repeated or reconciliation patterns still run.",
    intendedSeverity: "normal",
    isAnomaly: false,
    accent: "#15803d",
    tint: "#f0fdf4",
  },
] as const;

export default function DashboardPage() {
  const { principal } = usePrincipal();
  const role = principal?.role ?? "agent";

  // Track the `?agent_id=` from the URL in state so the SWR key changes
  // and the cache invalidates as soon as the user navigates to a
  // different agent (e.g. from the ops list). We also re-read on each
  // render so deep-links work after a hard refresh.
  const [urlAgentId, setUrlAgentId] = useState<number>(() => readAgentIdFromUrl());
  useEffect(() => {
    const sync = () => setUrlAgentId(readAgentIdFromUrl());
    sync();
    window.addEventListener("popstate", sync);
    // The ops list uses <a href="?agent_id=..."> which triggers a same-tab
    // navigation but NOT a popstate — patch pushState/replaceState to fire.
    const origPush = window.history.pushState;
    const origReplace = window.history.replaceState;
    window.history.pushState = function (...args) {
      const r = origPush.apply(this, args as any);
      sync();
      return r;
    };
    window.history.replaceState = function (...args) {
      const r = origReplace.apply(this, args as any);
      sync();
      return r;
    };
    return () => {
      window.removeEventListener("popstate", sync);
      window.history.pushState = origPush;
      window.history.replaceState = origReplace;
    };
  }, []);

  // Cache key MUST include role + agent_id, not just username. If two
  // different roles pick the same username, or the same user flips
  // between agent and ops, SWR would otherwise return the previous
  // shape (stale agent view → slow apparent switch).
  const cacheKey = ["dashboard", principal?.username ?? null, role, urlAgentId];

  const { data, mutate, error, isLoading } = useSWR<DashboardSummary>(
    cacheKey,
    () => client.getDashboard(urlAgentId).then(d => d as unknown as DashboardSummary),
    {
      refreshInterval: 15000,
      // Keep the previous role's summary on screen while the new one loads —
      // otherwise the page goes blank for ~1 round trip on every role switch,
      // which is what made the dashboard feel "way too slow" when toggling.
      keepPreviousData: true,
      // Suppress duplicate in-flight requests within this window. The agent
      // view fans out to N provider charts at 8s refresh; this keeps rapid
      // role flips from queuing overlapping fetches.
      dedupingInterval: 2000,
    }
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [openAlertId, setOpenAlertId] = useState<number | null>(null);
  const [scenarioFeedback, setScenarioFeedback] = useState<ScenarioFeedback>(null);

  async function tick() {
    setBusy("tick");
    try {
      await client.tickSimulation();
      await mutate();
    }
    finally { setBusy(null); }
  }
  async function inject(scenario: DemoScenario) {
    setBusy(scenario.kind);
    setScenarioFeedback(null);
    try {
      await client.injectScenario({
        kind: scenario.kind,
        label: scenario.title,
        provider: scenario.provider,
        intended_severity: scenario.intendedSeverity,
        is_anomaly: scenario.isAnomaly,
        duration_minutes: 8,
      });
      // A scenario record alone does not produce evidence. Advance one full
      // analytical cycle so every demo button has an immediate, observable result.
      const cycle = await client.tickSimulation();
      await mutate();
      const alertText = cycle.new_alerts.length === 1
        ? "1 advisory alert created or refreshed"
        : `${cycle.new_alerts.length} advisory alerts created or refreshed`;
      const qualityText = scenario.kind === "rocket_delay"
        ? ` · Rocket feed quality ${Math.round((cycle.data_quality.rocket ?? 0) * 100)}%`
        : "";
      setScenarioFeedback({
        tone: "success",
        title: `${scenario.title} completed`,
        detail: `${cycle.ticked} synthetic transactions analyzed · ${alertText}${qualityText}. No real financial action was executed.`,
      });
    } catch (e: any) {
      setScenarioFeedback({
        tone: "error",
        title: `${scenario.title} could not run`,
        detail: String(e?.message || e),
      });
    } finally { setBusy(null); }
  }

  const headerTitle = (() => {
    if (!data) return "Dashboard";
    switch (data.view) {
      case "agent":      return `Liquidity & Risk · ${data.display_name ?? "Agent"}`;
      case "ops":        return `Network Coordination · ${data.scope?.area ?? "—"}`;
      case "risk":       return `Risk Analyst Review Queue`;
      case "provider":   return `${(data.scope?.provider || "").toString().toUpperCase()} Provider View`;
      case "management": return `Executive Risk Overview`;
      default:           return "Dashboard";
    }
  })();
  const headerSub = (() => {
    if (!data) return "Loading…";
    switch (data.view) {
      case "agent":      return `${data.area} · agent ${data.agent_code} · acting as ${principal?.display_name ?? "agent"}`;
      case "ops":        return `${data.scope?.agent_count ?? 0} agents in your area · acting as ${principal?.display_name ?? "ops"}`;
      case "risk":       return `${data.scope?.queue_size ?? 0} escalated cases · acting as ${principal?.display_name ?? "risk"}`;
      case "provider":   return `${data.scope?.agent_count ?? 0} agents under coverage · acting as ${principal?.display_name ?? "provider"}`;
      case "management": return `${data.scope?.area_count ?? 0} areas · acting as ${principal?.display_name ?? "management"}`;
      default:           return "";
    }
  })();

  return (
    <>
      <PageHeader
        title={headerTitle}
        subtitle={headerSub}
        right={can(role, "can_inject_scenario") && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button onClick={tick} disabled={busy !== null} style={btn("#0f172a", "#fff")}>
              {busy === "tick" ? "Ticking…" : "Tick simulation"}
            </button>
          </div>
        )}
      />
      <Disclaimer />

      {error && <div style={{ color: "#dc2626" }}>API error: {String(error)}</div>}
      {/* First-ever load: no cached data yet, show the spinner. On later
          role switches keepPreviousData leaves `data` populated with the
          previous role, so this branch doesn't fire and the UI stays put. */}
      {isLoading && !data && <div>Loading…</div>}
      {/* Tail-end loading hint for role switches: a tiny inline pill so the
          user knows the new view is on its way. We deliberately don't
          unmount the previous view — keepPreviousData does the heavy
          lifting and avoids the layout flash. */}
      {data && isLoading && (
        <div style={{
          position: "fixed", bottom: 18, right: 18, zIndex: 50,
          background: "rgba(15,23,42,0.92)", color: "#f8fafc",
          padding: "8px 14px", borderRadius: 999, fontSize: 13, fontWeight: 600,
          boxShadow: "0 4px 12px rgba(0,0,0,0.18)",
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <span style={{
            width: 10, height: 10, borderRadius: 5,
            background: "#38bdf8", display: "inline-block",
            animation: "saPulse 1.2s ease-in-out infinite",
          }} />
          Loading {role} view…
        </div>
      )}

      {data?.view === "agent"      && <AgentView      data={data} role={role} busy={busy} inject={inject} scenarioFeedback={scenarioFeedback} onPickAlert={setOpenAlertId} openAlertId={openAlertId} onActionTaken={() => { void mutate(); }} />}
      {data?.view === "ops"        && <OpsView        data={data} />}
      {data?.view === "risk"       && <RiskView       data={data} />}
      {data?.view === "provider"   && <ProviderView   data={data} />}
      {data?.view === "management" && <ManagementView data={data} />}
    </>
  );
}

// ============================================================================
// AGGREGATE PRESSURE — earliest independent constraint. Physical cash and
// provider wallets remain visibly separate and are never added or converted.
// ============================================================================
function OperationalLiquidityCard({ aggregate }: { aggregate?: OperationalLiquiditySummary }) {
  if (!aggregate) return null;
  const conf = Math.round((aggregate.confidence ?? 0) * 100);
  const dq   = Math.round((aggregate.data_quality ?? 0) * 100);
  const fallback = !!aggregate.fallback_active;

  const headlineBg = fallback
    ? "#f1f5f9"
    : aggregate.limiting_hours_to_shortage != null && aggregate.limiting_hours_to_shortage < 2
      ? "#fee2e2"
      : aggregate.limiting_hours_to_shortage != null && aggregate.limiting_hours_to_shortage < 6
        ? "#fef9c3"
        : "#dcfce7";

  return (
    <Card style={{ marginBottom: 18, background: headlineBg, border: 0, padding: 22 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 28, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px", minWidth: 280 }}>
          <div style={{ fontSize: 13, color: "#475569", letterSpacing: 1.2, textTransform: "uppercase", fontWeight: 600 }}>
            Aggregate pressure · independent positions
          </div>
          <div style={{ fontSize: 26, fontWeight: 800, marginTop: 5, letterSpacing: -0.3 }}>
            {aggregate.pressure_label}
          </div>
          <div style={{ fontSize: 14, color: "#475569", marginTop: 4 }}>
            Earliest reliable constraint: <b>{aggregate.limiting_position ?? "none projected"}</b>
            {aggregate.limiting_position ? <> · {aggregate.shortage_eta_human}</> : null}
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: 14, color: "#334155", lineHeight: 1.7, minWidth: 220 }}>
          Physical cash on counter:&nbsp;
            <b>৳ {(aggregate.physical_cash ?? 0).toLocaleString()}</b><br />
          Provider wallets:&nbsp;<b>{aggregate.provider_count} separate positions</b><br />
          Separation:&nbsp;<b>not convertible or pooled</b>
        </div>
      </div>

      {/* Confidence + fallback band — surfaces explicitly whenever the
          projection is below confidence threshold or data quality is low */}
      <div style={{
        marginTop: 14,
        padding: "10px 12px",
        background: fallback ? "#fef3c7" : "#f8fafc",
        border: fallback ? "1px solid #fcd34d" : "1px solid #e2e8f0",
        borderRadius: 8,
        fontSize: 13,
        color: fallback ? "#92400e" : "#475569",
      }}>
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
          <span>
            Projection confidence:&nbsp;
            <b style={{ color: conf >= 60 ? "#15803d" : conf >= 30 ? "#a16207" : "#b91c1c" }}>
              {conf}%
            </b>
          </span>
          <span>Data quality (worst provider):&nbsp;<b>{dq}%</b></span>
          <span>
            Positions tracked:&nbsp;<b>{aggregate.provider_count + 1}</b>
          </span>
          {fallback && (
            <span style={{
              background: "#fde68a", color: "#92400e",
              padding: "2px 8px", borderRadius: 999, fontWeight: 700, fontSize: 12,
            }}>
              Fallback mode
            </span>
          )}
        </div>
        {(aggregate.notes ?? []).length > 0 && (
          <ul style={{ margin: "8px 0 0 0", padding: "0 0 0 18px" }}>
            {aggregate.notes.map((n, i) => <li key={i} style={{ marginBottom: 2 }}>{n}</li>)}
          </ul>
        )}
      </div>
    </Card>
  );
}

// ============================================================================
// AGENT VIEW — the screenshot-matching refined dashboard.
// ============================================================================
function AgentView({ data, role, busy, inject, scenarioFeedback, onPickAlert, openAlertId, onActionTaken }: {
  data: DashboardSummary; role: string; busy: string | null;
  inject: (scenario: DemoScenario) => Promise<void>;
  scenarioFeedback: ScenarioFeedback;
  onPickAlert: (id: number | null) => void;
  openAlertId: number | null;
  // Refresh callback fired when the user takes a recommended action from
  // the Decision Intelligence panel — re-fetches the dashboard summary so
  // the case state change + any re-fusion of signals show up immediately.
  onActionTaken?: () => void;
}) {
  const { data: open } = useSWR(
    openAlertId ? ["alert", openAlertId] : null,
    () => client.getAlert(openAlertId as number),
    { refreshInterval: 15000 }
  );
  const { data: ownSupport, mutate: refreshOwnSupport } = useSWR(
    ["cash-support", "agent", data.agent_id],
    () => client.getCashSupportRequests(),
    { refreshInterval: 5000 },
  );

  const providers = (data.providers ?? []).filter(p => p.provider !== "physical");
  const physical   = (data.providers ?? []).find(p => p.provider === "physical") ?? null;

  const scoreBg = (data.overall_score ?? 0) >= 70 ? "#fee2e2"
                : (data.overall_score ?? 0) >= 40 ? "#fef9c3" : "#dcfce7";

  return (
    <>
      {(data.operational_contexts ?? []).map((context, i) => (
        <Card key={`${context.provider}-${context.kind}-${i}`} style={{ marginBottom: 12, background: "#eff6ff", borderColor: "#93c5fd" }}>
          <div style={{ fontSize: 11, color: "#1d4ed8", textTransform: "uppercase", letterSpacing: 1 }}>
            Observed operational context · {context.provider}
          </div>
          <div style={{ fontWeight: 700, marginTop: 3 }}>{context.note}</div>
          <div style={{ fontSize: 12, color: "#475569", marginTop: 3 }}>
            Source: {context.source} · expires {new Date(context.ends_at).toLocaleString()} · contextual evidence, not an evaluation label
          </div>
        </Card>
      ))}
      <OperationalLiquidityCard aggregate={data.aggregate} />
      <Card style={{ marginBottom: 18, background: scoreBg, border: 0, padding: 22 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 28 }}>
          <div>
            <div style={{ fontSize: 13, color: "#64748b", letterSpacing: 1.2, textTransform: "uppercase", fontWeight: 600 }}>
              Overall pressure score
            </div>
            <div style={{ fontSize: 56, fontWeight: 800, lineHeight: 1.02, letterSpacing: -1 }}>{data.overall_score} / 100</div>
            <div style={{ fontSize: 16, color: "#475569", marginTop: 6 }}>{data.overall_reason}</div>
          </div>
          <div style={{ textAlign: "right", fontSize: 15, color: "#475569", lineHeight: 1.7 }}>
            Physical cash: <b>৳ {(data.physical_cash ?? 0).toLocaleString()}</b><br />
            Open alerts: <b>{(data.alerts ?? []).filter(a => a.status !== "resolved" && a.status !== "closed").length}</b><br />
            Data quality: <b>{data.providers?.length ? `${Math.round((data.providers.reduce((s,p) => s+p.data_quality,0) / data.providers.length) * 100)}%` : "—"}</b>
          </div>
        </div>
      </Card>

      {/* Provider liquidity cards — each card now pairs with a real
          time-series chart (x/y axes + burn-rate projection to ৳0).
          The grid fills the wider page (4 cols on wide screens, 2 on
          narrow) so we don't get dead space on either side. */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))",
        gap: 18,
        marginBottom: 20,
      }}>
        {[physical, ...providers].filter(Boolean).map(p => (
          <ProviderChartCard key={p!.provider} p={p!} agentId={data.agent_id ?? 1} />
        ))}
      </div>

      {/* Decision Intelligence — what should I do next? (replaces the
          Forward-Looking Forecast panel: every provider card now carries
          its own forecast, so the global section is freed up for the
          prioritized recommended-action view.)
          onActionTaken refreshes SWR so the case state change and any
          resulting re-fusion show up immediately. */}
      <DecisionRecommendationPanel
        data={data}
        supportRequests={ownSupport?.requests ?? []}
        onActionTaken={onActionTaken}
        onSupportChanged={() => { void refreshOwnSupport(); }}
      />

      {(ownSupport?.requests?.length ?? 0) > 0 && (
        <Card style={{ marginTop: 16, borderColor: "#fdba74" }}>
          <div style={{ fontSize: 12, color: "#9a3412", textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>
            My cash-support requests
          </div>
          {ownSupport!.requests.map(row => (
            <div key={row.id} style={{ display: "flex", justifyContent: "space-between", gap: 12, paddingTop: 10, marginTop: 10, borderTop: "1px solid #f1f5f9" }}>
              <div>
                <b>{row.provider.toUpperCase()}</b> · Alert #{row.alert_id}
                <div style={{ fontSize: 14, marginTop: 3 }}>Requested: <b>৳{(row.amount ?? 0).toLocaleString()}</b></div>
                <div style={{ color: "#64748b", fontSize: 13, marginTop: 3 }}>{row.provider_note || row.note}</div>
                {row.status === "fulfilled" && <div style={{ color: "#166534", fontSize: 13, marginTop: 3 }}>Support coordination recorded complete; no wallet transaction was executed by this platform.</div>}
              </div>
              <b style={{ color: row.status === "rejected" ? "#dc2626" : row.status === "fulfilled" ? "#16a34a" : "#c2410c" }}>
                {row.status.toUpperCase()}
              </b>
            </div>
          ))}
        </Card>
      )}

      {/* Scenario injectors — role-gated */}
      {can(role, "can_inject_scenario") && (
        <Card style={{ marginTop: 18, marginBottom: 18, padding: 20, borderColor: "#cbd5e1" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 12, color: "#475569", letterSpacing: 1.1, textTransform: "uppercase", fontWeight: 800 }}>
                Synthetic scenario lab
              </div>
              <div style={{ fontSize: 20, fontWeight: 750, marginTop: 3 }}>Demonstrate a complete decision-support cycle</div>
              <div style={{ fontSize: 13, color: "#64748b", marginTop: 5, maxWidth: 760, lineHeight: 1.5 }}>
                Each control injects synthetic data, advances the simulation, recomputes forecasts and review signals,
                and refreshes this dashboard. Results remain advisory and require human review.
              </div>
            </div>
            <div style={{ padding: "5px 9px", borderRadius: 999, background: "#f1f5f9", color: "#475569", fontSize: 11, fontWeight: 700 }}>
              SYNTHETIC ONLY · NO REAL TRANSACTIONS
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 12, marginTop: 16 }}>
            {SCENARIOS.map(s => (
              <div key={s.kind} style={{ display: "flex", flexDirection: "column", minHeight: 238, padding: 14, borderRadius: 10, border: `1px solid ${s.accent}33`, borderTop: `4px solid ${s.accent}`, background: s.tint }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: .8, color: s.accent, fontWeight: 800 }}>{s.category}</span>
                  <span style={{ fontSize: 10, color: "#64748b", background: "#fff", borderRadius: 999, padding: "2px 7px", border: "1px solid #e2e8f0" }}>{s.provider.toUpperCase()}</span>
                </div>
                <div style={{ fontSize: 16, fontWeight: 750, marginTop: 8, color: "#0f172a" }}>{s.title}</div>
                <div style={{ fontSize: 12, color: "#475569", lineHeight: 1.45, marginTop: 6 }}>{s.description}</div>
                <div style={{ fontSize: 11, color: "#64748b", lineHeight: 1.45, marginTop: 9, paddingTop: 8, borderTop: "1px solid rgba(100,116,139,.18)" }}>
                  <b style={{ color: "#334155" }}>Expected:</b> {s.expected}
                </div>
                <button
                  onClick={() => { void inject(s); }}
                  disabled={busy !== null}
                  style={{
                    marginTop: "auto", width: "100%", border: 0, borderRadius: 7,
                    padding: "8px 10px", background: busy === null || busy === s.kind ? s.accent : "#cbd5e1",
                    color: "#fff", fontSize: 12, fontWeight: 750,
                    cursor: busy === null ? "pointer" : "not-allowed",
                    opacity: busy !== null && busy !== s.kind ? .65 : 1,
                  }}
                >
                  {busy === s.kind ? "Running analysis…" : "Run scenario"}
                </button>
              </div>
            ))}
          </div>

          {scenarioFeedback && (
            <div role="status" aria-live="polite" style={{
              marginTop: 14, padding: "11px 13px", borderRadius: 8,
              background: scenarioFeedback.tone === "success" ? "#ecfdf5" : "#fef2f2",
              border: `1px solid ${scenarioFeedback.tone === "success" ? "#86efac" : "#fca5a5"}`,
              color: scenarioFeedback.tone === "success" ? "#166534" : "#991b1b",
            }}>
              <div style={{ fontSize: 13, fontWeight: 800 }}>{scenarioFeedback.title}</div>
              <div style={{ fontSize: 12, marginTop: 2, lineHeight: 1.45 }}>{scenarioFeedback.detail}</div>
            </div>
          )}
        </Card>
      )}

      {/* Active alerts */}
      <h2 style={{ fontSize: 22, fontWeight: 700, marginTop: 12, marginBottom: 12, letterSpacing: -0.2 }}>
        Active alerts ({(data.alerts ?? []).length})
      </h2>
      {(data.alerts ?? []).length === 0 && (
        <Card><div style={{ color: "#64748b", fontSize: 15 }}>All clear. No active alerts.</div></Card>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {(data.alerts ?? []).map(a => (
          <a key={a.id} href={`/alerts/${a.id}`} onClick={(e) => { e.preventDefault(); onPickAlert(a.id); }} style={{ textDecoration: "none", color: "inherit" }}>
            <AlertCard alert={a} />
          </a>
        ))}
      </div>

      {/* The "what action to take next" panel — opens when an alert card is clicked */}
      {openAlertId && (
        <Card style={{ marginTop: 18 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 700, fontSize: 17 }}>Take action on Alert #{openAlertId}</div>
            <button onClick={() => onPickAlert(null)} style={{ background: "transparent", border: 0, color: "#94a3b8", cursor: "pointer", fontSize: 14 }}>close</button>
          </div>
          {open
            ? <AlertActionStrip alert={open} onChanged={() => { /* swr auto-refresh */ }} />
            : <div style={{ color: "#94a3b8", fontSize: 14 }}>Loading…</div>}
        </Card>
      )}
    </>
  );
}

// ============================================================================
// OPS VIEW — list agents in my area with per-agent pressure and dispatch hint.
// ============================================================================
function OpsView({ data }: { data: DashboardSummary }) {
  const list = data.per_agent ?? [];
  return (
    <>
      <Card style={{ marginBottom: 16, background: "#ecfeff", borderColor: "#67e8f9" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 13, color: "#155e75", letterSpacing: 1, textTransform: "uppercase", fontWeight: 600 }}>
              Network Coordination
            </div>
            <div style={{ fontSize: 26, fontWeight: 700 }}>{list.length} agents under {data.scope?.area ?? "—"} coverage</div>
            <div style={{ fontSize: 15, color: "#155e75" }}>
              Your job: assign field officers, balance cash across outlets, escalate persistent cases.
            </div>
          </div>
        </div>
      </Card>
      {list.length === 0 && (
        <Card>
          <div style={{ color: "#64748b", fontSize: 15 }}>
            No agents yet in <b>{data.scope?.area ?? "your area"}</b>. Seed additional agents to populate this view.
          </div>
        </Card>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {list.map(a => (
          <a key={a.agent_id} href={`/dashboard?agent_id=${a.agent_id}`} style={{ textDecoration: "none", color: "inherit" }}>
            <Card>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 17 }}>{a.display_name}</div>
                  <div style={{ fontSize: 13, color: "#94a3b8" }}>{a.agent_code} · {a.area}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 13, color: "#64748b" }}>Pressure</div>
                  <div style={{ fontSize: 26, fontWeight: 700, color: a.overall_score >= 70 ? "#dc2626" : a.overall_score >= 40 ? "#ca8a04" : "#16a34a" }}>
                    {a.overall_score}/100
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 14, color: "#475569", marginTop: 10 }}>{a.overall_reason}</div>
              <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
                {(a.providers ?? []).map(p => (
                  <span key={p.provider} style={{
                    background: p.health === "critical" ? "#fee2e2"
                              : p.health === "high"     ? "#ffedd5"
                              : p.health === "low"      ? "#fef9c3"
                              : p.health === "unknown"  ? "#e5e7eb" : "#dcfce7",
                    color: "#0f172a", padding: "4px 10px", borderRadius: 999, fontSize: 13, fontWeight: 600,
                  }}>
                    {p.provider}: {p.balance != null ? `৳${p.balance.toLocaleString()}` : "—"}
                  </span>
                ))}
              </div>
              <div style={{ fontSize: 13, color: "#dc2626", marginTop: 10, fontWeight: 600 }}>
                {(a.alerts ?? []).filter(x => x.status !== "resolved" && x.status !== "closed").length} open alerts
              </div>
            </Card>
          </a>
        ))}
      </div>
    </>
  );
}

// ============================================================================
// RISK VIEW — escalated compliance queue.
// ============================================================================
function RiskView({ data }: { data: DashboardSummary }) {
  const list = data.queue ?? [];
  return (
    <>
      <Card style={{ marginBottom: 16, background: "#f5f3ff", borderColor: "#c4b5fd" }}>
        <div style={{ fontSize: 11, color: "#5b21b6", letterSpacing: 1, textTransform: "uppercase" }}>
          Risk analyst review queue
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{list.length} escalated cases</div>
        <div style={{ fontSize: 13, color: "#5b21b6" }}>
          Escalated cases only. Review evidence and record advisory recommendations; no final wrongdoing determination is made here.
        </div>
      </Card>
      <div style={{ display: "grid", gap: 12 }}>
        {list.length === 0 && <Card><div style={{ color: "#64748b" }}>Queue empty. No escalated cases.</div></Card>}
        {list.map(a => (
          <a key={a.id} href={`/alerts/${a.id}`} style={{ textDecoration: "none", color: "inherit" }}>
            <Card>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ flex: 1 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 11, color: "#94a3b8" }}>Alert #{a.id} · {a.provider ?? "—"}</span>
                    <span style={{ fontSize: 11, color: "#64748b" }}>priority {a.priority_score}/100</span>
                  </div>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{a.title}</div>
                  <div style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>{a.summary}</div>
                  <div style={{ fontSize: 12, color: "#6d28d9", marginTop: 5 }}>
                    Confidence {Math.round(a.confidence * 100)}% · {a.evidence?.length ?? 0} evidence item(s) · Requires Human Review
                  </div>
                </div>
                <div style={{ textAlign: "right", minWidth: 120 }}>
                  <div style={{ fontSize: 11, color: "#64748b" }}>Status</div>
                  <div style={{ fontWeight: 700 }}>{a.status}</div>
                </div>
              </div>
            </Card>
          </a>
        ))}
      </div>
    </>
  );
}

// ============================================================================
// PROVIDER VIEW — only this provider's column across the agents they cover.
// ============================================================================
function ProviderView({ data }: { data: DashboardSummary }) {
  const list = data.per_agent ?? [];
  const prov = data.scope?.provider;
  const { data: supportData, mutate: refreshSupport } = useSWR(
    ["cash-support", prov],
    () => client.getCashSupportRequests(),
    { refreshInterval: 5000 },
  );
  const [supportBusy, setSupportBusy] = useState<number | null>(null);
  const [supportError, setSupportError] = useState<string | null>(null);
  const supportRequests = supportData?.requests ?? [];
  async function actOnSupport(row: CashSupportRequest, action: "acknowledge" | "approve" | "reject" | "fulfil") {
    setSupportBusy(row.id);
    setSupportError(null);
    try {
      await client.actOnCashSupport(row.id, action);
      await refreshSupport();
    } catch (e: any) {
      setSupportError(String(e?.message || e));
    } finally {
      setSupportBusy(null);
    }
  }
  // Defense-in-depth: even if the server forgets to scrub, refuse to render
  // anything that doesn't match the principal's provider.
  const onlyOwn = (provs?: DashboardProvider[]) =>
    (provs ?? []).filter(p => p && p.provider === prov);
  return (
    <>
      <Card style={{ marginBottom: 16, background: "#faf5ff", borderColor: "#ddd6fe" }}>
        <div style={{ fontSize: 11, color: "#5b21b6", letterSpacing: 1, textTransform: "uppercase" }}>
          Provider coverage
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{(prov ?? "—").toString().toUpperCase()} · {list.length} agents</div>
        <div style={{ fontSize: 13, color: "#5b21b6" }}>
          You only see this provider's column. Other providers are walled out.
        </div>
      </Card>
      <Card style={{ marginBottom: 16, borderColor: supportRequests.some(r => r.status === "requested") ? "#fb923c" : "#e5e7eb" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 12, color: "#9a3412", textTransform: "uppercase", letterSpacing: 1, fontWeight: 700 }}>
              Cash-support notifications
            </div>
            <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4 }}>
              {supportRequests.filter(r => !["rejected", "fulfilled"].includes(r.status)).length} active request(s)
            </div>
          </div>
          <span style={{ fontSize: 24 }}>🔔</span>
        </div>
        {supportRequests.length === 0 ? (
          <div style={{ color: "#64748b", marginTop: 12 }}>No agents are currently requesting provider support.</div>
        ) : supportRequests.map(row => (
          <div key={row.id} style={{ marginTop: 12, padding: 12, border: "1px solid #e5e7eb", borderRadius: 8, background: row.status === "requested" ? "#fff7ed" : "#fff" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <div>
                <b>{row.agent_name}</b> <span style={{ color: "#64748b" }}>({row.agent_code})</span>
                <div style={{ fontSize: 16, marginTop: 4 }}>Forecast-sized request: <b>৳{(row.amount ?? 0).toLocaleString()}</b></div>
                <div style={{ fontSize: 13, color: "#475569", marginTop: 4 }}>{row.note}</div>
                <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>{row.calculation}</div>
                {row.forecast_balance != null && <div style={{ fontSize: 12, color: "#64748b", marginTop: 3 }}>Balance at request: ৳{row.forecast_balance.toLocaleString()} · target: ৳{(row.target_balance ?? 0).toLocaleString()} · coverage: {row.coverage_hours}h</div>}
                {row.status === "fulfilled" && <div style={{ fontSize: 13, color: "#166534", fontWeight: 700, marginTop: 4 }}>✓ Support coordination recorded complete · no wallet transaction executed</div>}
                <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>Alert #{row.alert_id} · {new Date(row.created_at).toLocaleString()}</div>
              </div>
              <b style={{ color: row.status === "rejected" ? "#dc2626" : row.status === "fulfilled" ? "#16a34a" : "#c2410c" }}>{row.status.toUpperCase()}</b>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
              {row.status === "requested" && <button disabled={supportBusy === row.id} onClick={() => actOnSupport(row, "acknowledge")} style={btn("#0ea5e9", "#fff")}>Acknowledge</button>}
              {["requested", "acknowledged"].includes(row.status) && <button disabled={supportBusy === row.id} onClick={() => actOnSupport(row, "approve")} style={btn("#16a34a", "#fff")}>Approve support plan · ৳{(row.amount ?? 0).toLocaleString()}</button>}
              {["requested", "acknowledged"].includes(row.status) && <button disabled={supportBusy === row.id} onClick={() => actOnSupport(row, "reject")} style={btn("#dc2626", "#fff")}>Reject</button>}
              {row.status === "approved" && <button disabled={supportBusy === row.id} onClick={() => actOnSupport(row, "fulfil")} style={btn("#7c3aed", "#fff")}>Mark fulfilled</button>}
            </div>
          </div>
        ))}
        {supportError && <div style={{ color: "#dc2626", fontSize: 13, marginTop: 10 }}>{supportError}</div>}
      </Card>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {list.map(a => {
          // Aggregate block spans independent positions — provider view drops it.
          // Aligned with server-side enforcement.
          const myCol = onlyOwn(a.providers)[0] ?? null;
          const myAlerts = (a.alerts ?? []).filter(al => al?.provider === prov);
          return (
            <Card key={a.agent_id}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{a.display_name}</div>
                  <div style={{ fontSize: 11, color: "#94a3b8" }}>{a.agent_code} · {a.area}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 11, color: "#64748b" }}>{(prov ?? "").toString().toUpperCase()} balance</div>
                  <div style={{ fontSize: 20, fontWeight: 700 }}>
                    {myCol?.balance != null ? `৳${myCol.balance.toLocaleString()}` : "—"}
                  </div>
                </div>
              </div>
              {myCol && (
                <div style={{ marginTop: 10 }}>
                  <ProviderLiquidityCard p={myCol} />
                </div>
              )}
              {/* In-card alert list is provider-scoped on the server; we
                  re-filter here as a backstop. */}
              {myAlerts.length > 0 && (
                <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
                  {myAlerts.map(al => (
                    <AlertCard key={al.id} alert={al} />
                  ))}
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </>
  );
}

// ============================================================================
// MANAGEMENT VIEW — risk-by-area, drill-down to agent.
// ============================================================================
function MgmtFilterBar({
  filters, onChange, areas, agents,
}: {
  filters: { provider: string; area: string; mgr_agent: string; since_minutes: string };
  onChange: (next: typeof filters) => void;
  areas: string[];
  agents: { id: number; code: string; display_name: string }[];
}) {
  const selectStyle: React.CSSProperties = {
    padding: "6px 8px", fontSize: 12, borderRadius: 6, border: "1px solid #cbd5e1",
    background: "white", color: "#0f172a",
  };
  return (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
      <label style={mgmtLbl}>
        <span>Provider</span>
        <select
          value={filters.provider}
          onChange={e => onChange({ ...filters, provider: e.target.value })}
          style={selectStyle}
        >
          <option value="">all</option>
          {["bkash", "nagad", "rocket", "physical"].map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </label>
      <label style={mgmtLbl}>
        <span>Area</span>
        <select
          value={filters.area}
          onChange={e => onChange({ ...filters, area: e.target.value })}
          style={selectStyle}
        >
          <option value="">all</option>
          {areas.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      </label>
      <label style={mgmtLbl}>
        <span>Agent</span>
        <select
          value={filters.mgr_agent}
          onChange={e => onChange({ ...filters, mgr_agent: e.target.value })}
          style={selectStyle}
        >
          <option value="">all</option>
          {agents.map(a => (
            <option key={a.id} value={String(a.id)}>{a.display_name} ({a.code})</option>
          ))}
        </select>
      </label>
      <label style={mgmtLbl}>
        <span>Window</span>
        <select
          value={filters.since_minutes}
          onChange={e => onChange({ ...filters, since_minutes: e.target.value })}
          style={selectStyle}
        >
          <option value="60">last 1h</option>
          <option value="360">last 6h</option>
          <option value="1440">last 24h</option>
          <option value="10080">last 7d</option>
          <option value="43200">last 30d</option>
        </select>
      </label>
      <button
        style={btn("#e2e8f0", "#0f172a")}
        onClick={() => onChange({ provider: "", area: "", mgr_agent: "", since_minutes: "1440" })}
      >
        Reset
      </button>
    </div>
  );
}

const mgmtLbl: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 2,
  fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5,
};

function PressureBucketBar({
  buckets,
}: { buckets: Record<string, number> | undefined }) {
  const order: { key: string; label: string; color: string }[] = [
    { key: "normal", label: "Normal", color: "#16a34a" },
    { key: "low", label: "Low", color: "#0ea5e9" },
    { key: "high", label: "High", color: "#ca8a04" },
    { key: "critical", label: "Critical", color: "#dc2626" },
    { key: "unknown", label: "Unknown", color: "#94a3b8" },
  ];
  const totals = buckets ?? {};
  const total = order.reduce((s, b) => s + (totals[b.key] || 0), 0) || 1;
  return (
    <div>
      <div style={{ display: "flex", height: 14, borderRadius: 6, overflow: "hidden", border: "1px solid #e5e7eb" }}>
        {order.map(b => {
          const n = totals[b.key] || 0;
          if (n === 0) return null;
          return (
            <div
              key={b.key}
              title={`${b.label}: ${n}`}
              style={{ width: `${(n / total) * 100}%`, background: b.color }}
            />
          );
        })}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 6, fontSize: 11 }}>
        {order.map(b => (
          <div key={b.key} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ width: 8, height: 8, background: b.color, borderRadius: 2 }} />
            <span style={{ color: "#475569" }}>{b.label}</span>
            <span style={{ fontWeight: 700, color: "#0f172a" }}>{totals[b.key] || 0}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function DataCompletenessBadge({
  completeness, total, incomplete, incompleteAreas,
}: {
  completeness: number; total: number; incomplete: number; incompleteAreas: number;
}) {
  const pct = Math.round(completeness * 100);
  const tone = completeness >= 0.9 ? "#16a34a" : completeness >= 0.6 ? "#ca8a04" : "#dc2626";
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "10px 14px", borderRadius: 10,
      background: "#f8fafc", border: "1px solid #e2e8f0",
    }}>
      <div>
        <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 }}>
          Data completeness
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: tone }}>{pct}%</div>
      </div>
      <div style={{ fontSize: 12, color: "#475569", lineHeight: 1.5 }}>
        <div><b style={{ color: "#0f172a" }}>{total - incomplete}</b> of {total} providers have signal.</div>
        {incomplete > 0 && (
          <div style={{ color: "#b45309" }}>
            <b>{incomplete}</b> incomplete · <b>{incompleteAreas}</b> area(s) flagged with partial data
          </div>
        )}
        {incomplete === 0 && (
          <div style={{ color: "#16a34a" }}>All tracked providers have recent forecast snapshots.</div>
        )}
      </div>
    </div>
  );
}

function RecurringProblemsList({
  items, anomalyRollup,
}: {
  items: { agent_id: number; agent_code: string; display_name: string; provider: string;
           open_window: number; open_24h: number; is_recurring_pattern: boolean }[];
  anomalyRollup: { area: string; provider: string; anomaly_count: number }[];
}) {
  const top = [...items].sort((a, b) => b.open_window - a.open_window).slice(0, 8);
  if (top.length === 0) {
    return (
      <div style={{ fontSize: 12, color: "#64748b" }}>
        No agents are currently in the open-alert window for any provider.
      </div>
    );
  }
  return (
    <div>
      <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "#94a3b8", borderBottom: "1px solid #e5e7eb" }}>
            <th style={mgmtTh}>Agent</th>
            <th style={mgmtTh}>Provider</th>
            <th style={{ ...mgmtTh, textAlign: "right" }}>Open (window)</th>
            <th style={{ ...mgmtTh, textAlign: "right" }}>Open (24h)</th>
            <th style={mgmtTh}>Pattern</th>
          </tr>
        </thead>
        <tbody>
          {top.map(r => (
            <tr key={`${r.agent_id}-${r.provider}`} style={{ borderBottom: "1px solid #f1f5f9" }}>
              <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                {r.display_name} <span style={{ color: "#94a3b8", fontWeight: 400 }}>({r.agent_code})</span>
              </td>
              <td style={{ padding: "6px 8px", color: "#475569" }}>{r.provider}</td>
              <td style={{ padding: "6px 8px", textAlign: "right", fontWeight: 700, color: r.open_window > 0 ? "#dc2626" : "#16a34a" }}>
                {r.open_window}
              </td>
              <td style={{ padding: "6px 8px", textAlign: "right", color: "#475569" }}>{r.open_24h}</td>
              <td style={{ padding: "6px 8px" }}>
                {r.is_recurring_pattern ? (
                  <span style={{
                    background: "#fee2e2", color: "#991b1b",
                    padding: "2px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600,
                  }}>
                    Recurring pattern
                  </span>
                ) : (
                  <span style={{ color: "#94a3b8" }}>—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {anomalyRollup && anomalyRollup.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 4 }}>
            Anomaly rollup · last 7 days · by area × provider
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {anomalyRollup.map((r, i) => (
              <div key={i} style={{
                fontSize: 11, padding: "4px 8px", borderRadius: 6,
                background: r.anomaly_count > 5 ? "#fee2e2" : r.anomaly_count > 0 ? "#fef3c7" : "#f1f5f9",
                color: r.anomaly_count > 5 ? "#991b1b" : r.anomaly_count > 0 ? "#854d0e" : "#475569",
              }}>
                <b>{r.area}</b> · {r.provider} · {r.anomaly_count} anomalies
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
const mgmtTh: React.CSSProperties = { padding: "6px 8px", fontWeight: 500, fontSize: 11 };

function ManagementView({ data }: { data: DashboardSummary }) {
  const [filters, setFilters] = useState({ provider: "", area: "", mgr_agent: "", since_minutes: "1440" });
  const { data: filteredData, isLoading: filtersLoading } = useSWR(
    ["management-dashboard", filters.provider, filters.area, filters.mgr_agent, filters.since_minutes],
    () => client.getDashboard(1, {
      provider: filters.provider || undefined,
      area: filters.area || undefined,
      agentId: filters.mgr_agent ? Number(filters.mgr_agent) : undefined,
      sinceMinutes: Number(filters.since_minutes),
    }),
    { keepPreviousData: true, dedupingInterval: 500 },
  );
  const viewData = filteredData?.view === "management" ? filteredData : data;
  const list = (viewData as any).areas ?? [];
  const network = (viewData as any).network ?? {};
  const scope = (viewData as any).scope ?? {};
  const recurring = (viewData as any).recurring_problems ?? {};
  const providerCount: number = scope.provider_count ?? 0;
  const incompleteCount: number = scope.incomplete_provider_count ?? 0;
  const completeness: number = scope.data_completeness ?? 1.0;
  const windowMinutes: number = recurring.window_minutes ?? 1440;

  // Keep filter options from the original unfiltered response.
  const baseList = (data as any).areas ?? [];
  const allAreas: string[] = Array.from(new Set(baseList.map((b: any) => b.area))).sort() as string[];
  const agentMap = new Map<number, { id: number; code: string; display_name: string }>();
  baseList.forEach((b: any) => (b.agents_detail || []).forEach((a: any) => {
    agentMap.set(a.agent_id, { id: a.agent_id, code: a.agent_code, display_name: a.display_name });
  }));
  const allAgents = Array.from(agentMap.values()).sort((a, b) => a.display_name.localeCompare(b.display_name));

  const incompleteAreas = list.filter((b: any) => b.agents_with_incomplete_data).length;

  return (
    <>
      <Card style={{ marginBottom: 16, background: "#f1f5f9", borderColor: "#cbd5e1" }}>
        <div style={{ fontSize: 11, color: "#475569", letterSpacing: 1, textTransform: "uppercase" }}>
          Executive overview
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{list.length} areas tracked</div>
        <div style={{ fontSize: 13, color: "#475569" }}>
          Read-only view aggregated by geography. Drill into an area for agent-level detail.
          Scroll down for network pressure, data completeness, and recurring problems across the window.
        </div>
        <div style={{ marginTop: 12 }}>
          <MgmtFilterBar
            filters={filters}
            onChange={setFilters}
            areas={allAreas}
            agents={allAgents}
          />
          {filtersLoading && <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>Applying server-side filters…</div>}
        </div>
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
        <Card>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 }}>
            Network pressure mix ({network.agents_total ?? "—"} agents · {network.agents_seen ?? "—"} seen)
          </div>
          <div style={{ fontSize: 14, color: "#0f172a", marginTop: 6 }}>
            <b>{network.open_alerts ?? 0}</b> open alerts · <b style={{ color: "#dc2626" }}>{network.critical_alerts ?? 0}</b> critical
          </div>
          <div style={{ marginTop: 10 }}>
            <PressureBucketBar buckets={network.pressure_buckets} />
          </div>
          <div style={{ marginTop: 10, fontSize: 11, color: "#64748b" }}>
            Avg network pressure <b style={{ color: "#0f172a" }}>{network.avg_pressure ?? 0}/100</b>
          </div>
        </Card>
        <DataCompletenessBadge
          completeness={completeness}
          total={providerCount}
          incomplete={incompleteCount}
          incompleteAreas={incompleteAreas}
        />
      </div>

      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <div>
            <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 0.5 }}>
              Recurring problems · last {windowMinutes < 1440 ? `${Math.round(windowMinutes / 60)}h` : windowMinutes === 1440 ? "24h" : `${Math.round(windowMinutes / 1440)}d`}
            </div>
            <div style={{ fontSize: 13, color: "#475569", marginTop: 2 }}>
              Counts show the selected window beside the last 24 hours. "Recurring pattern" means at least two open review signals in the selected window.
            </div>
          </div>
          <div style={{ fontSize: 12, color: "#475569" }}>
            Showing <b style={{ color: "#0f172a" }}>{(recurring.by_agent || []).length}</b> agent-provider pairs with open alerts
          </div>
        </div>
        <div style={{ marginTop: 10 }}>
          <RecurringProblemsList
            items={recurring.by_agent || []}
            anomalyRollup={recurring.anomaly_rollup || []}
          />
        </div>
      </Card>

      <div style={{ display: "grid", gap: 12 }}>
        {list.map((b: any, i: number) => (
          <Card key={`${b.area}-${i}`}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 16 }}>{b.area}</div>
                <div style={{ fontSize: 12, color: "#64748b" }}>
                  {b.agents} agents · {b.open_alerts} open · {b.critical_alerts} critical
                  {b.agents_with_incomplete_data && (
                    <span style={{ marginLeft: 8, color: "#b45309", fontWeight: 600 }}>
                      · partial data
                    </span>
                  )}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, color: "#64748b" }}>Avg pressure</div>
                <div style={{
                  fontSize: 26, fontWeight: 700,
                  color: b.avg_pressure >= 70 ? "#dc2626" : b.avg_pressure >= 40 ? "#ca8a04" : "#16a34a",
                }}>
                  {b.avg_pressure}/100
                </div>
              </div>
            </div>

            {(b.pressure_buckets || b.top_reason) && (
              <div style={{ marginTop: 8 }}>
                {b.top_reason && (
                  <div style={{ fontSize: 12, color: "#475569", marginBottom: 6 }}>
                    <b style={{ color: "#0f172a" }}>Why:</b> {b.top_reason}
                  </div>
                )}
                {b.pressure_buckets && (
                  <PressureBucketBar buckets={b.pressure_buckets} />
                )}
              </div>
            )}

            {b.agents_detail && b.agents_detail.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#94a3b8", borderBottom: "1px solid #e5e7eb" }}>
                      <th style={{ padding: "4px 6px" }}>Agent</th>
                      <th style={{ padding: "4px 6px" }}>Code</th>
                      <th style={{ padding: "4px 6px", textAlign: "right" }}>Pressure</th>
                      <th style={{ padding: "4px 6px", textAlign: "right" }}>Open alerts</th>
                      <th style={{ padding: "4px 6px" }}>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {b.agents_detail.map((a: any) => (
                      <tr key={a.agent_id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        <td style={{ padding: "4px 6px", fontWeight: 600 }}>{a.display_name}</td>
                        <td style={{ padding: "4px 6px", color: "#64748b" }}>{a.agent_code}</td>
                        <td style={{
                          padding: "4px 6px", textAlign: "right", fontWeight: 700,
                          color: a.overall_score >= 70 ? "#dc2626" : a.overall_score >= 40 ? "#ca8a04" : "#16a34a",
                        }}>{a.overall_score}</td>
                        <td style={{ padding: "4px 6px", textAlign: "right" }}>{a.open_alerts}</td>
                        <td style={{ padding: "4px 6px", color: "#475569", fontSize: 11 }}>
                          {a.overall_reason || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        ))}
        {list.length === 0 && (
          <div style={{ fontSize: 12, color: "#94a3b8", padding: 12 }}>
            No areas match the current server-side filters.
          </div>
        )}
      </div>
    </>
  );
}

function btn(bg: string, fg: string): React.CSSProperties {
  return {
    background: bg, color: fg, border: 0, borderRadius: 8,
    padding: "8px 14px", fontSize: 13, fontWeight: 600, cursor: "pointer",
  };
}
