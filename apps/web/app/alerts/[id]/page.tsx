"use client";
import React from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { client } from "../../../lib/client";
import { Card, Confidence, Disclaimer, PageHeader, SeverityPill, StatusPill } from "../../../components/Primitives";
import { AlertActionStrip } from "../../../components/AlertActionStrip";
import { usePrincipal } from "../../../components/PrincipalProvider";
import { RoleGuard } from "../../../components/RoleGuard";

export default function AlertDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { data, mutate, error } = useSWR(["alert", id], () => client.getAlert(id), { refreshInterval: 15000 });
  const { principal } = usePrincipal();
  const role = (principal?.role ?? "agent") as any;

  if (error) return <div style={{ color: "#dc2626" }}>Error loading alert: {String(error)}</div>;
  if (!data) return <div>Loading…</div>;

  return (
    <>
      <PageHeader
        title={`Alert #${data.id} · ${data.provider || "—"}`}
        subtitle={`${data.fused_explanation}`}
        right={<div style={{ display: "flex", gap: 8 }}>
          <SeverityPill severity={data.severity} />
          <StatusPill status={data.status} />
        </div>}
      />
      <Disclaimer />

      <Card style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700 }}>{data.title}</div>
            <div style={{ marginTop: 8, color: "#334155", lineHeight: 1.5 }}>{data.summary}</div>
          </div>
          <div style={{ textAlign: "right", minWidth: 140 }}>
            <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase" }}>Priority</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{data.priority_score}/100</div>
            <Confidence value={data.confidence} />
            <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2, fontStyle: "italic" }}>
              triage signal — not a fraud verdict
            </div>
          </div>
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: "#64748b" }}>
          Owner: <b>{data.owner_label}</b> · Initial trigger: <b>{data.initial_owner}</b>
        </div>
      </Card>

      <Card style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>Why this was flagged — and why it may still be benign</h3>
        <div style={{ marginTop: 6, fontSize: 12, color: "#475569", lineHeight: 1.5 }}>
          Every reason and evidence line below is <b>supporting context</b> for your
          review. The model surfaces patterns it cannot prove intent for — it is
          not a fraud verdict and not a proof of wrongdoing. False positives
          are expected; an honest review may find the pattern has a benign
          explanation (e.g. salary day, festival, provider outage).
        </div>
        <ul style={{ marginTop: 8, paddingLeft: 18 }}>
          {data.reasons.map((r, i) => <li key={i} style={{ fontSize: 13, color: "#475569" }}>{r}</li>)}
        </ul>
      </Card>

      <Card style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>Evidence ({data.evidence.length})</h3>
        <div style={{ marginTop: 4, fontSize: 11, color: "#64748b" }}>
          Each entry below names the source (anomaly rule or forecast reason)
          and the rule that fired it. Use it to reconstruct the trigger.
        </div>
        <ul style={{ marginTop: 6, paddingLeft: 18 }}>
          {data.evidence.map((e, i) => (
            <li key={i} style={{ fontSize: 12, color: "#475569" }}>
              <span style={{ fontWeight: 600 }}>[{e.source}{e.rule ? ` / ${e.rule}` : ""}]</span> {e.text}
            </li>
          ))}
          {data.evidence.length === 0 && <li style={{ fontSize: 12, color: "#94a3b8" }}>No granular evidence logged.</li>}
        </ul>
      </Card>

      <Card style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>Recommended actions</h3>
        {data.recommended_actions.map((r, i) => (
          <div key={i} style={{ borderTop: i ? "1px solid #e5e7eb" : 0, paddingTop: i ? 8 : 0, marginTop: i ? 8 : 6 }}>
            <div style={{ fontWeight: 600 }}>{r.label}</div>
            <div style={{ fontSize: 12, color: "#64748b" }}>
              key: <code>{r.key}</code> · weight {r.weight}
            </div>
          </div>
        ))}
      </Card>

      {data.case && (
        <Card style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>Case #{data.case.id} · state: <code>{data.case.state}</code></h3>
          <h4 style={{ fontSize: 13, marginTop: 10 }}>Audit trail</h4>
          {data.case.audit.map((h, i) => (
            <div key={i} style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>
              <b>{new Date(h.ts).toLocaleString()}</b> · {h.actor}: {h.from_state || "—"} → <b>{h.to_state}</b> · {h.reason}
            </div>
          ))}
          <h4 style={{ fontSize: 13, marginTop: 10 }}>Notes</h4>
          {data.case.notes.map((n, i) => (
            <div key={i} style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>
              <b>{new Date(n.ts).toLocaleString()}</b> · {n.user}: {n.text}
            </div>
          ))}
        </Card>
      )}

      <RoleGuard capability="can_act_on_alerts">
          <Card>
            <h3 style={{ margin: 0, fontSize: 14 }}>Take action (state-machine)</h3>
            <p style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
              Current state: <code>{data.case?.state || "none"}</code>. Only transitions you are permitted to perform are enabled — others are shown greyed.
            </p>
            <AlertActionStrip alert={data} onChanged={mutate} />            <div style={{
              marginTop: 12, padding: "10px 12px",
              background: "#f1f5f9", border: "1px solid #cbd5e1",
              borderRadius: 6, fontSize: 12, color: "#334155",
            }}>
              <b>Audit record.</b> Your decision (acknowledge, review, resolve,
              escalate, decision, or close) is the final compliance ruling for
              this case. It is timestamped and recorded on the case audit trail
              above — escalation_engine auto-routing never decides outcomes.
              Add a note explaining your reasoning; it is the only place your
              justification is preserved.
            </div>          </Card>
        </RoleGuard>
        {role !== "risk" && (
          <div style={{ marginTop: 12, fontSize: 12, color: "#94a3b8" }}>
            Compliance decisions and final close are restricted to the <b>risk</b> role. Escalating forwards it to them.
          </div>
        )}
      </>
    );
  }