"use client";
import React, { useState } from "react";
import useSWR from "swr";
import { client } from "../../lib/client";
import { Card, Disclaimer, PageHeader } from "../../components/Primitives";
import { AlertCard } from "../../components/AlertCard";
import { ProviderLiquidityCard } from "../../components/ProviderLiquidityCard";
import { ForwardLookingForecast } from "../../components/ForwardLookingForecast";
import { AlertActionStrip } from "../../components/AlertActionStrip";
import { usePrincipal } from "../../components/PrincipalProvider";
import { can } from "../../lib/rbac";
import type { DashboardSummary } from "../../lib/types";

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
      <Card style={{ marginBottom: 16, background: scoreBg, border: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 11, color: "#64748b", letterSpacing: 1, textTransform: "uppercase" }}>
              Overall pressure score
            </div>
            <div style={{ fontSize: 32, fontWeight: 700 }}>{data.overall_score} / 100</div>
            <div style={{ fontSize: 13, color: "#475569" }}>{data.overall_reason}</div>
          </div>
          <div style={{ textAlign: "right", fontSize: 12, color: "#475569" }}>
            Physical cash: <b>৳ {(data.physical_cash ?? 0).toLocaleString()}</b><br />
            Open alerts: <b>{(data.alerts ?? []).filter(a => a.status !== "resolved" && a.status !== "closed").length}</b><br />
            Data quality: <b>{data.providers?.length ? `${Math.round((data.providers.reduce((s,p) => s+p.data_quality,0) / data.providers.length) * 100)}%` : "—"}</b>
          </div>
        </div>
      </Card>

      {/* Provider liquidity cards — the screenshot */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
        {[physical, ...providers].filter(Boolean).map(p => (
          <ProviderLiquidityCard key={p!.provider} p={p!} />
        ))}
      </div>

      {/* Forward-looking forecast */}
      <ForwardLookingForecast providers={providers} physical={physical} />

      {/* Scenario injectors — role-gated */}
      {can(role, "can_inject_scenario") && (
        <Card style={{ marginTop: 16, marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: "#64748b", letterSpacing: 1, textTransform: "uppercase", marginBottom: 8 }}>
            Inject what-if scenario (for demo)
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {SCENARIOS.map(s => (
              <button key={s.kind} onClick={() => inject(s.kind)} disabled={busy === s.kind} style={btn(s.color, "#fff")}>
                {busy === s.kind ? "…" : s.label}
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* Active alerts */}
      <h2 style={{ fontSize: 16, fontWeight: 700, marginTop: 8 }}>
        Active alerts ({(data.alerts ?? []).length})
      </h2>
      {(data.alerts ?? []).length === 0 && (
        <Card><div style={{ color: "#64748b" }}>All clear. No active alerts.</div></Card>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {(data.alerts ?? []).map(a => (
          <a key={a.id} href={`/alerts/${a.id}`} onClick={(e) => { e.preventDefault(); onPickAlert(a.id); }} style={{ textDecoration: "none", color: "inherit" }}>
            <AlertCard alert={a} />
          </a>
        ))}
      </div>

      {/* The "what action to take next" panel — opens when an alert card is clicked */}
      {openAlertId && (
        <Card style={{ marginTop: 16 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>Take action on Alert #{openAlertId}</div>
            <button onClick={() => onPickAlert(null)} style={{ background: "transparent", border: 0, color: "#94a3b8", cursor: "pointer" }}>close</button>
          </div>
          {open
            ? <AlertActionStrip alert={open} onChanged={() => { /* swr auto-refresh */ }} />
            : <div style={{ color: "#94a3b8", fontSize: 12 }}>Loading…</div>}
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
            <div style={{ fontSize: 11, color: "#155e75", letterSpacing: 1, textTransform: "uppercase" }}>
              Network Coordination
            </div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>{list.length} agents under {data.scope?.area ?? "—"} coverage</div>
            <div style={{ fontSize: 13, color: "#155e75" }}>
              Your job: assign field officers, balance cash across outlets, escalate persistent cases.
            </div>
          </div>
        </div>
      </Card>
      {list.length === 0 && (
        <Card>
          <div style={{ color: "#64748b" }}>
            No agents yet in <b>{data.scope?.area ?? "your area"}</b>. Seed additional agents to populate this view.
          </div>
        </Card>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {list.map(a => (
          <a key={a.agent_id} href={`/dashboard?agent_id=${a.agent_id}`} style={{ textDecoration: "none", color: "inherit" }}>
            <Card>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{a.display_name}</div>
                  <div style={{ fontSize: 11, color: "#94a3b8" }}>{a.agent_code} · {a.area}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ fontSize: 11, color: "#64748b" }}>Pressure</div>
                  <div style={{ fontSize: 22, fontWeight: 700, color: a.overall_score >= 70 ? "#dc2626" : a.overall_score >= 40 ? "#ca8a04" : "#16a34a" }}>
                    {a.overall_score}/100
                  </div>
                </div>
              </div>
              <div style={{ fontSize: 12, color: "#475569", marginTop: 8 }}>{a.overall_reason}</div>
              <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
                {(a.providers ?? []).map(p => (
                  <span key={p.provider} style={{
                    background: p.health === "critical" ? "#fee2e2"
                              : p.health === "high"     ? "#ffedd5"
                              : p.health === "low"      ? "#fef9c3"
                              : p.health === "unknown"  ? "#e5e7eb" : "#dcfce7",
                    color: "#0f172a", padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 600,
                  }}>
                    {p.provider}: {p.balance != null ? `৳${p.balance.toLocaleString()}` : "—"}
                  </span>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "#dc2626", marginTop: 8, fontWeight: 600 }}>
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
          const myCol = (a.providers ?? []).find(p => p.provider === prov);
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