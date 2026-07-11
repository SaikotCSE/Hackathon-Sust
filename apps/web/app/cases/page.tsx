"use client";
import React from "react";
import useSWR from "swr";
import { client } from "../../lib/client";
import { Card, Disclaimer, PageHeader } from "../../components/Primitives";
import { usePrincipal } from "../../components/PrincipalProvider";
import { can } from "../../lib/rbac";

export default function CasesPage() {
  const { data } = useSWR("cases", () => client.getAlerts(), { refreshInterval: 15000 });
  const { principal } = usePrincipal();
  const role = (principal?.role ?? "") as any;

  if (!can(role, "can_view_cases")) {
    return (
      <>
        <PageHeader title="Cases" subtitle="Restricted" />
        <Disclaimer />
        <Card><div style={{ color: "#64748b" }}>Your role does not have access to the case queue.</div></Card>
      </>
    );
  }

  // Risk role gets priority sort + filter to escalated/compliance_decision.
  const isRisk = role === "risk";
  const list = (data?.alerts ?? [])
    .filter(a => a.case && !["resolved", "closed"].includes(a.status))
    .filter(a => isRisk ? ["escalated", "compliance_decision"].includes(a.status) : true)
    .sort((a, b) => b.priority_score - a.priority_score);

  return (
    <>
      <PageHeader
        title={isRisk ? "Compliance Queue" : "Cases"}
        subtitle={isRisk
          ? "Escalated + compliance-decision state. Final ruling reserved for risk."
          : "State machine: assigned → acknowledged → review → resolved · with audit trail."}
      />
      <Disclaimer />
      <div style={{ display: "grid", gap: 12 }}>
        {list.map(a => (
          <Card key={a.id}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 1 }}>
                  Case #{a.case?.id} · state <code>{a.case?.state}</code>
                </div>
                <div style={{ fontWeight: 600, marginTop: 4 }}>{a.title}</div>
                <div style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>
                  {a.provider ?? "—"} · {a.severity} · owner: {a.owner_label}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, color: "#94a3b8" }}>priority</div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{a.priority_score}</div>
                <a href={`/alerts/${a.id}`} style={{ textDecoration: "none", color: "#0ea5e9", fontSize: 13 }}>Open →</a>
              </div>
            </div>
          </Card>
        ))}
        {list.length === 0 && <div style={{ color: "#64748b" }}>No open cases.</div>}
      </div>
    </>
  );
}