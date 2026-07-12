"use client";
import React, { useState } from "react";
import useSWR from "swr";
import { client } from "../../lib/client";
import { Disclaimer, PageHeader } from "../../components/Primitives";
import { AlertCard } from "../../components/AlertCard";
import type { Severity, AlertStatus } from "../../lib/types";

const STATUSES: (AlertStatus | "all")[] = ["all", "open", "assigned", "acknowledged", "under_review", "resolved", "closed"];
const SEVERITIES: (Severity | "all")[] = ["all", "critical", "high", "low", "normal"];

export default function AlertsPage() {
  const [status, setStatus] = useState<typeof STATUSES[number]>("all");
  const [severity, setSeverity] = useState<typeof SEVERITIES[number]>("all");
  const { data, isLoading, error } = useSWR(
    ["alerts", status, severity],
    () => client.getAlerts({
      status: status === "all" ? undefined : status,
      severity: severity === "all" ? undefined : severity,
    }),
    { refreshInterval: 5000 }
  );
  return (
    <>
      <PageHeader title="Alerts" subtitle="Every alert is explainable and links to a case with a full audit trail." />
      <Disclaimer />
      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <label style={{ fontSize: 12, color: "#64748b" }}>Status:</label>
        <select value={status} onChange={(e) => setStatus(e.target.value as any)} style={selectStyle}>
          {STATUSES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <label style={{ fontSize: 12, color: "#64748b", marginLeft: 12 }}>Severity:</label>
        <select value={severity} onChange={(e) => setSeverity(e.target.value as any)} style={selectStyle}>
          {SEVERITIES.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "#64748b" }}>
          {data?.alerts.length ?? 0} match{data?.alerts.length === 1 ? "" : "es"}
        </span>
      </div>
      <div style={{ display: "grid", gap: 12 }}>
        {isLoading && <div>Loading…</div>}
        {error && <div style={{ color: "#dc2626" }}>Error: {String(error)}</div>}
        {data?.alerts.map(a => (
          <a key={a.id} href={`/alerts/${a.id}`} style={{ textDecoration: "none", color: "inherit" }}>
            <AlertCard alert={a} />
          </a>
        ))}
        {data && data.alerts.length === 0 && <div style={{ color: "#64748b" }}>No alerts match the filter.</div>}
      </div>
    </>
  );
}

const selectStyle: React.CSSProperties = {
  border: "1px solid #cbd5e1", borderRadius: 6, padding: "4px 8px", fontSize: 13, background: "#fff",
};
