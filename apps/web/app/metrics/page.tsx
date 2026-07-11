"use client";
import React, { useState } from "react";
import useSWR from "swr";
import { client } from "../../lib/client";
import { Card, Disclaimer, PageHeader } from "../../components/Primitives";
import { usePrincipal } from "../../components/PrincipalProvider";
import { can } from "../../lib/rbac";

export default function MetricsPage() {
  const { data, mutate: mutateMetrics } = useSWR("metrics", () => client.getMetrics(), { refreshInterval: 8000 });
  const { data: weights, mutate: mutateWeights } = useSWR("weights", () => client.getDecisionWeights());
  const { data: scenarios } = useSWR("scenarios", () => client.getScenarios(), { refreshInterval: 10000 });
  const { data: dq } = useSWR("dq", () => client.getDataQualityEvents(), { refreshInterval: 10000 });
  const [busy, setBusy] = useState(false);
  const { principal } = usePrincipal();
  const role = (principal?.role ?? "") as any;
  const canReload = can(role, "can_reload_weights");
  const canView   = can(role, "can_view_metrics");

  async function reloadWeights() {
    if (!canReload) return;
    setBusy(true);
    try {
      await client.reloadDecisionWeights();
      await mutateWeights();
      await mutateMetrics();
    } finally { setBusy(false); }
  }

  if (!canView) {
    return (
      <>
        <PageHeader title="Operational Metrics" subtitle="Restricted" />
        <Disclaimer />
        <Card><div style={{ color: "#64748b" }}>Your role does not have access to operational metrics.</div></Card>
      </>
    );
  }

  if (!data) return <div>Loading…</div>;

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <>
      <PageHeader
        title="Operational Metrics"
        subtitle={`Module 9 — the seven required metrics computed against simulation ground truth.${!canReload ? " · (read-only — risk role reloads weights)" : ""}`}
        right={canReload && (
          <button onClick={reloadWeights} disabled={busy} style={btn("#0f172a", "#fff")}>
            {busy ? "Reloading…" : "Reload decision-weights.json"}
          </button>
        )}
      />
      <Disclaimer />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <Stat label="Liquidity MAE (min)"  value={data.liquidity_mae_minutes.toFixed(1)} />
        <Stat label="Lead time (min)"       value={data.shortage_lead_time_minutes.toFixed(1)} />
        <Stat label="Anomaly precision"     value={pct(data.anomaly_precision)} />
        <Stat label="Anomaly recall"        value={pct(data.anomaly_recall)} />
        <Stat label="False positive rate"   value={pct(data.false_positive_rate)} />
        <Stat label="Explanation coverage"  value={pct(data.explanation_coverage)} />
        <Stat label="API p50 (ms)"          value={data.api_latency_p50_ms.toFixed(1)} />
        <Stat label="API p95 (ms)"          value={data.api_latency_p95_ms.toFixed(1)} />
        <Stat label="Confidence Δ under bad data" value={data.confidence_delta_under_bad_data.toFixed(2)} />
        <Stat label="Priority alignment"    value={data.priority_classification_alignment == null ? "n/a" : pct(data.priority_classification_alignment)} />
        <Stat label="Total alerts"          value={String(data.alert_count)} />
        <Stat label="Total anomaly events"  value={String(data.anomaly_event_count)} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 16 }}>
        <Card>
          <h3 style={{ margin: 0, fontSize: 14 }}>Active decision weights</h3>
          <pre style={{ fontSize: 11, background: "#f8fafc", padding: 12, borderRadius: 6, overflow: "auto", marginTop: 6 }}>
{JSON.stringify(weights?.providers, null, 2)}
          </pre>
        </Card>
        <Card>
          <h3 style={{ margin: 0, fontSize: 14 }}>Recent scenarios (ground truth)</h3>
          <div style={{ marginTop: 6, maxHeight: 220, overflow: "auto", fontSize: 12 }}>
            {scenarios?.scenarios.slice(0, 10).map(s => (
              <div key={s.id} style={{ padding: "4px 0", borderBottom: "1px solid #e5e7eb" }}>
                <b>{s.kind}</b> · {s.provider} · intended: {s.intended_severity}
                {s.is_anomaly_ground_truth && <span style={{ color: "#dc2626" }}> · anomaly-GT</span>}
                <div style={{ color: "#94a3b8", fontSize: 11 }}>{new Date(s.injected_at).toLocaleString()} · {s.note}</div>
              </div>
            ))}
            {(!scenarios?.scenarios || scenarios.scenarios.length === 0) && (
              <div style={{ color: "#94a3b8" }}>No scenarios yet — try injecting one from the dashboard.</div>
            )}
          </div>
        </Card>
      </div>

      <Card style={{ marginTop: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>Data-quality feed events</h3>
        <div style={{ marginTop: 6, fontSize: 12 }}>
          {dq?.events.map(e => (
            <div key={e.id} style={{ padding: "4px 0", borderBottom: "1px solid #e5e7eb" }}>
              <b>{e.provider}</b> · {e.issue} · started {new Date(e.started_at).toLocaleString()}
              {e.resolved_at ? <> · <span style={{ color: "#16a34a" }}>resolved {new Date(e.resolved_at).toLocaleString()}</span></> : <> · <span style={{ color: "#dc2626" }}>open</span></>}
              <div style={{ color: "#94a3b8", fontSize: 11 }}>{e.note}</div>
            </div>
          ))}
          {(!dq?.events || dq.events.length === 0) && (
            <div style={{ color: "#94a3b8" }}>No feed issues — all providers healthy.</div>
          )}
        </div>
      </Card>
    </>
  );
}

function Stat({ label, value }: { label: string; value: any }) {
  return (
    <Card>
      <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 1 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{value}</div>
    </Card>
  );
}

function btn(bg: string, fg: string): React.CSSProperties {
  return { background: bg, color: fg, border: 0, borderRadius: 6, padding: "6px 12px", fontSize: 13, fontWeight: 600, cursor: "pointer" };
}