"use client";
import React, { useState } from "react";
import useSWR from "swr";
import { client } from "../../lib/client";
import { Card, Disclaimer, PageHeader } from "../../components/Primitives";
import { AlertCard } from "../../components/AlertCard";
import { ProviderLiquidityCard } from "../../components/ProviderLiquidityCard";
import { ProviderChartCard } from "../../components/ProviderChartCard";
import { DecisionRecommendationPanel } from "../../components/DecisionRecommendationPanel";
import { AlertActionStrip } from "../../components/AlertActionStrip";
import { usePrincipal } from "../../components/PrincipalProvider";
import { can } from "../../lib/rbac";
import type { DashboardSummary, CombinedView, DashboardAlert } from "../../lib/types";

const SCENARIOS = [
  { kind: "bkash_surge",      label: "Inject: bKash surge (critical liquidity)",   color: "#dc2626" },
  { kind: "repeated_amount",  label: "Inject: Repeated amounts (anomaly)",         color: "#7c3aed" },
  { kind: "structuring",      label: "Inject: Structuring near 5,000 BDT",          color: "#9333ea" },
  { kind: "rocket_delay",     label: "Inject: Rocket feed delay (data quality)",    color: "#0891b2" },
  { kind: "salary_day",       label: "Inject: Salary day (legit spike — should NOT alert)", color: "#16a34a" },
] as const;

export default function DashboardPage() {
  const { principal } = usePrincipal();
  const role = principal?.role ?? "agent";
  const { data, mutate, error, isLoading } = useSWR<DashboardSummary>(
    ["dashboard", principal?.username],
    () => client.getDashboard().then(d => d as unknown as DashboardSummary),
    { refreshInterval: 5000 }
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [openAlertId, setOpenAlertId] = useState<number | null>(null);

  async function tick() {
    setBusy("tick");
    try { await client.tickSimulation(); mutate(); }
    finally { setBusy(null); }
  }
  async function inject(kind: string, provider?: string) {
    setBusy(kind);
    try {
      await client.injectScenario({ kind: kind as any, provider, duration_minutes: 8 });
      mutate();
    } finally { setBusy(null); }
  }

  const headerTitle = (() => {
    if (!data) return "Dashboard";
    switch (data.view) {
      case "agent":      return `Liquidity & Risk · ${data.display_name ?? "Agent"}`;
      case "ops":        return `Network Coordination · ${data.scope?.area ?? "—"}`;
      case "risk":       return `Risk / Compliance Queue`;
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
            <button onClick={tick} disabled={busy === "tick"} style={btn("#0f172a", "#fff")}>
              {busy === "tick" ? "Ticking…" : "Tick simulation"}
            </button>
          </div>
        )}
      />
      <Disclaimer />

      {error && <div style={{ color: "#dc2626" }}>API error: {String(error)}</div>}
      {isLoading && !data && <div>Loading…</div>}

      {data?.view === "agent"      && <AgentView      data={data} role={role} busy={busy} inject={inject} onPickAlert={setOpenAlertId} openAlertId={openAlertId} />}
      {data?.view === "ops"        && <OpsView        data={data} onSelectAgent={() => mutate()} />}
      {data?.view === "risk"       && <RiskView       data={data} />}
      {data?.view === "provider"   && <ProviderView   data={data} />}
      {data?.view === "management" && <ManagementView data={data} />}
    </>
  );
}

// ============================================================================
// COMBINED PICTURE — one card showing cash + every e-money balance as a
// single pool with a shared hours-to-shortage projection. Surfaces the
// confidence + fallback state so the agent never sees a confident-looking
// number that the system can't actually defend.
// ============================================================================
function CombinedPictureCard({ combined }: { combined?: CombinedView }) {
  if (!combined) return null;
  const conf = Math.round((combined.confidence ?? 0) * 100);
  const dq   = Math.round((combined.data_quality ?? 0) * 100);
  const fallback = !!combined.fallback_active;

  const headlineBg = fallback
    ? "#f1f5f9"
    : combined.hours_to_shortage != null && combined.hours_to_shortage < 2
      ? "#fee2e2"
      : combined.hours_to_shortage != null && combined.hours_to_shortage < 6
        ? "#fef9c3"
        : "#dcfce7";

  return (
    <Card style={{ marginBottom: 18, background: headlineBg, border: 0, padding: 22 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 28, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px", minWidth: 280 }}>
          <div style={{ fontSize: 13, color: "#475569", letterSpacing: 1.2, textTransform: "uppercase", fontWeight: 600 }}>
            Combined picture — cash on counter + every e-money balance
          </div>
          <div style={{ fontSize: 36, fontWeight: 800, marginTop: 4, letterSpacing: -0.5 }}>
            ৳ {(combined.total_cash ?? 0).toLocaleString()}
          </div>
          <div style={{ fontSize: 16, color: "#1e293b", marginTop: 6, fontWeight: 600 }}>
            {combined.healthy_label ?? "projection unavailable right now"}
          </div>
          <div style={{ fontSize: 14, color: "#475569", marginTop: 4 }}>
            You can keep serving customers for the next{" "}
            <b>{combined.can_serve_hours_text ?? "—"}</b>.
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: 14, color: "#334155", lineHeight: 1.7, minWidth: 220 }}>
          Physical cash on counter:&nbsp;
            <b>৳ {(combined.physical_cash ?? 0).toLocaleString()}</b><br />
          E-money balances (all providers):&nbsp;
            <b>৳ {(combined.total_emoney ?? 0).toLocaleString()}</b><br />
          Combined burn rate:&nbsp;
            <b>৳ {(combined.combined_burn_per_min ?? 0).toFixed(2)} / min</b><br />
          Shared hours to shortage:&nbsp;
            <b>{combined.shortage_eta_human ?? "no projection"}</b>
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
            Providers with burn-rate signal:&nbsp;
            <b>{combined.providers_with_burn_signal ?? 0}</b> / {combined.providers_with_shortage_projection != null ? "—" : "—"}
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
        {(combined.notes ?? []).length > 0 && (
          <ul style={{ margin: "8px 0 0 0", padding: "0 0 0 18px" }}>
            {combined.notes.map((n, i) => <li key={i} style={{ marginBottom: 2 }}>{n}</li>)}
          </ul>
        )}
      </div>
    </Card>
  );
}

// ============================================================================
// BANGLA / BANGLISH ALERT — same live alert data, rendered in Banglish so
// the agent can verify the numbers match the English view. Strings are
// generated from the live alert (not hardcoded) — provider, severity,
// priority score and balance all come from the API response.
// ============================================================================
function BanglaAlertExample({ alerts }: { alerts: DashboardAlert[] }) {
  const a = (alerts ?? [])[0];
  if (!a) {
    return (
      <Card style={{ marginBottom: 18, background: "#f8fafc", border: 0, padding: 18 }}>
        <div style={{ fontSize: 13, color: "#64748b", letterSpacing: 1, textTransform: "uppercase", fontWeight: 600, marginBottom: 8 }}>
          বাংলা উদাহরণ (Banglish preview)
        </div>
        <div style={{ fontSize: 14, color: "#475569" }}>
          No active alert right now — Banglish preview not generated. (English view above shows <b>All clear</b>.)
        </div>
      </Card>
    );
  }
  // Translate severity / status labels from the live alert — nothing is
  // hardcoded. Provider name + priority score + balance come from `a`.
  const severity_bn: Record<string, string> = {
    normal:   "স্বাভাবিক",
    low:      "নিম্ন",
    high:     "উচ্চ",
    critical: "অত্যন্ত জরুরি",
  };
  const status_bn: Record<string, string> = {
    open: "নতুন",
    acknowledged: "গৃহীত",
    review: "পর্যালোচনাধীন",
    escalated: "উর্ধ্বতন কর্তৃপক্ষের কাছে",
    resolved: "সমাধান হয়েছে",
    closed: "বন্ধ",
  };
  const sev_bn = severity_bn[a.severity] ?? a.severity;
  const stat_bn = status_bn[a.status] ?? a.status;
  // Banglish headline — derived from the live alert so provider name AND
  // numbers (id, priority, severity, status) all match the English card
  // above, instead of being hardcoded to "bKash".
  const provider_bn = (a.provider ?? "").toString().toUpperCase();
  const headline_bn =
    `${provider_bn} প্রোভাইডারে অস্বাভাবিক কার্যকলাপ লক্ষ্য করা গেছে — ` +
    `পর্যালোচনা প্রয়োজন (Alert #${a.id}, priority ${a.priority_score}/100, ` +
    `severity: ${sev_bn}, status: ${stat_bn}).`;

  return (
    <Card style={{ marginBottom: 18, background: "#fef9c3", borderColor: "#facc15", padding: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        <span style={{
          fontSize: 11, color: "#92400e", letterSpacing: 1, textTransform: "uppercase",
          fontWeight: 700, padding: "2px 8px", background: "#fde68a", borderRadius: 999,
        }}>
          বাংলা উদাহরণ · Banglish
        </span>
        <span style={{ fontSize: 12, color: "#92400e" }}>
          Same live alert — numbers below match the English card above.
        </span>
      </div>
      <div style={{ fontSize: 15, color: "#0f172a", fontWeight: 600, marginBottom: 4 }}>
        {headline_bn}
      </div>
      <div style={{ fontSize: 13, color: "#475569" }}>
        সারসংক্ষেপ (summary): {a.summary}
      </div>
      <div style={{ fontSize: 12, color: "#64748b", marginTop: 6 }}>
        এই সিস্টেম কখনো লেনদেন সম্পাদন করে না এবং কখনো জালিয়াতির অভিযোগ করে না — সিদ্ধান্ত আপনার।
      </div>
    </Card>
  );
}

// ============================================================================
// AGENT VIEW — the screenshot-matching refined dashboard.
// ============================================================================
function AgentView({ data, role, busy, inject, onPickAlert, openAlertId }: {
  data: DashboardSummary; role: string; busy: string | null;
  inject: (k: string, p?: string) => void;
  onPickAlert: (id: number | null) => void;
  openAlertId: number | null;
}) {
  const { data: open } = useSWR(
    openAlertId ? ["alert", openAlertId] : null,
    () => client.getAlert(openAlertId as number),
    { refreshInterval: 5000 }
  );

  const providers = (data.providers ?? []).filter(p => p.provider !== "physical");
  const physical   = (data.providers ?? []).find(p => p.provider === "physical") ?? null;

  const scoreBg = (data.overall_score ?? 0) >= 70 ? "#fee2e2"
                : (data.overall_score ?? 0) >= 40 ? "#fef9c3" : "#dcfce7";

  return (
    <>
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
          prioritized recommended-action view.) */}
      <DecisionRecommendationPanel data={data} />

      {/* Bangla / Banglish example — same live alert, Bengali render.
          Hidden when there are no alerts (we don't fabricate example data). */}
      <BanglaAlertExample alerts={data.alerts ?? []} />

      {/* Scenario injectors — role-gated */}
      {can(role, "can_inject_scenario") && (
        <Card style={{ marginTop: 18, marginBottom: 18 }}>
          <div style={{ fontSize: 13, color: "#64748b", letterSpacing: 1, textTransform: "uppercase", marginBottom: 10, fontWeight: 600 }}>
            Inject what-if scenario (for demo)
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {SCENARIOS.map(s => (
              <button key={s.kind} onClick={() => inject(s.kind)} disabled={busy === s.kind} style={btn(s.color, "#fff")}>
                {busy === s.kind ? "…" : s.label}
              </button>
            ))}
          </div>
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
function OpsView({ data, onSelectAgent }: { data: DashboardSummary; onSelectAgent: () => void }) {
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
          Compliance queue
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{list.length} escalated cases</div>
        <div style={{ fontSize: 13, color: "#5b21b6" }}>
          Final decisions live here. Only Risk / Compliance may issue a compliance decision or close a case.
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
  // Defense-in-depth: even if the server forgets to scrub, refuse to render
  // anything that doesn't match the principal's provider.
  const onlyOwn = (provs?: { provider?: string }[]) =>
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
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {list.map(a => {
          // Combined pool block is cross-provider by definition — drop it.
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
                    <AlertCard key={al.id} a={al as any} />
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
function ManagementView({ data }: { data: DashboardSummary }) {
  const list = data.areas ?? [];
  return (
    <>
      <Card style={{ marginBottom: 16, background: "#f1f5f9", borderColor: "#cbd5e1" }}>
        <div style={{ fontSize: 11, color: "#475569", letterSpacing: 1, textTransform: "uppercase" }}>
          Executive overview
        </div>
        <div style={{ fontSize: 22, fontWeight: 700 }}>{list.length} areas tracked</div>
        <div style={{ fontSize: 13, color: "#475569" }}>
          Read-only view aggregated by geography. Drill into an area for agent-level detail.
        </div>
      </Card>
      <div style={{ display: "grid", gap: 12 }}>
        {list.map((b, i) => (
          <Card key={i}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 16 }}>{b.area}</div>
                <div style={{ fontSize: 12, color: "#64748b" }}>
                  {b.agents} agents · {b.open_alerts} open · {b.critical_alerts} critical
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, color: "#64748b" }}>Avg pressure</div>
                <div style={{ fontSize: 26, fontWeight: 700, color: b.avg_pressure >= 70 ? "#dc2626" : b.avg_pressure >= 40 ? "#ca8a04" : "#16a34a" }}>
                  {b.avg_pressure}/100
                </div>
              </div>
            </div>
            <div style={{ marginTop: 10 }}>
              <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "#94a3b8", borderBottom: "1px solid #e5e7eb" }}>
                    <th style={{ padding: "4px 6px" }}>Agent</th>
                    <th style={{ padding: "4px 6px" }}>Code</th>
                    <th style={{ padding: "4px 6px", textAlign: "right" }}>Pressure</th>
                    <th style={{ padding: "4px 6px", textAlign: "right" }}>Open alerts</th>
                  </tr>
                </thead>
                <tbody>
                  {b.agents_detail.map(a => (
                    <tr key={a.agent_id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "4px 6px", fontWeight: 600 }}>{a.display_name}</td>
                      <td style={{ padding: "4px 6px", color: "#64748b" }}>{a.agent_code}</td>
                      <td style={{ padding: "4px 6px", textAlign: "right", fontWeight: 700, color: a.overall_score >= 70 ? "#dc2626" : a.overall_score >= 40 ? "#ca8a04" : "#16a34a" }}>{a.overall_score}</td>
                      <td style={{ padding: "4px 6px", textAlign: "right" }}>{a.open_alerts}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))}
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