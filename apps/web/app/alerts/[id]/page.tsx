"use client";
import React, { useState } from "react";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { client } from "../../../lib/client";
import { Card, Confidence, Disclaimer, PageHeader, SeverityPill, StatusPill } from "../../../components/Primitives";
import { AlertActionStrip } from "../../../components/AlertActionStrip";
import { usePrincipal } from "../../../components/PrincipalProvider";
import { RoleGuard } from "../../../components/RoleGuard";
import { isProviderOperations } from "../../../lib/rbac";

export default function AlertDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { data, mutate, error } = useSWR(["alert", id], () => client.getAlert(id), { refreshInterval: 15000 });
  const { principal } = usePrincipal();
  const role = (principal?.role ?? "agent") as any;
  const canCoordinateOperations = isProviderOperations(role, principal?.username);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [coordinationComment, setCoordinationComment] = useState("");
  const [coordinationBusy, setCoordinationBusy] = useState<string | null>(null);
  const [riskRecommendation, setRiskRecommendation] = useState("requires_human_review");
  const [riskComment, setRiskComment] = useState("");
  const [explanationLanguage, setExplanationLanguage] = useState<"en" | "bn" | "banglish">("en");

  async function saveReviewNote() {
    if (!reviewNote.trim()) return;
    setReviewBusy(true); setReviewError(null);
    try { await client.addReviewerNote(id, reviewNote.trim()); setReviewNote(""); await mutate(); }
    catch (e: any) { setReviewError(String(e?.message || e)); }
    finally { setReviewBusy(false); }
  }

  async function regenerateExplanation() {
    setReviewBusy(true); setReviewError(null);
    try { await client.regenerateExplanation(id, explanationLanguage); await mutate(); }
    catch (e: any) { setReviewError(String(e?.message || e)); }
    finally { setReviewBusy(false); }
  }

  async function coordinate(action: string, target?: string) {
    if (!coordinationComment.trim()) return;
    setCoordinationBusy(action); setReviewError(null);
    try { await client.coordinateCase(id, action, coordinationComment.trim(), target); setCoordinationComment(""); await mutate(); }
    catch (e: any) { setReviewError(String(e?.message || e)); }
    finally { setCoordinationBusy(null); }
  }

  async function saveRiskRecommendation() {
    if (!riskComment.trim()) return;
    setReviewBusy(true); setReviewError(null);
    try { await client.recordRiskRecommendation(id, riskRecommendation, riskComment.trim()); setRiskComment(""); await mutate(); }
    catch (e: any) { setReviewError(String(e?.message || e)); }
    finally { setReviewBusy(false); }
  }

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

      {data.case && (
        <Card style={{ marginBottom: 12, borderColor: "#bae6fd", background: "#f8fcff" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
            <div>
              <h3 style={{ margin: 0, fontSize: 14 }}>AI-assisted human-review explanation</h3>
              <div style={{ fontSize: 11, color: "#64748b", marginTop: 3 }}>
                {data.case.explanation_provider} · {data.case.explanation_model} · {data.case.explanation_status}
              </div>
            </div>
            {["agent", "ops", "risk", "provider"].includes(role) && <div style={{ display: "flex", gap: 6 }}>
              <select value={explanationLanguage} onChange={e => setExplanationLanguage(e.target.value as "en" | "bn" | "banglish")} style={{ border: "1px solid #7dd3fc", borderRadius: 6, padding: "6px 8px", background: "#fff" }}>
                <option value="en">English</option><option value="bn">বাংলা</option><option value="banglish">Banglish</option>
              </select>
              <button disabled={reviewBusy} onClick={regenerateExplanation} style={{ border: "1px solid #7dd3fc", background: "#fff", color: "#0369a1", borderRadius: 6, padding: "6px 10px", cursor: "pointer" }}>
                Generate explanation
              </button>
            </div>}
          </div>
          <div style={{ marginTop: 10, color: "#0f172a", lineHeight: 1.5 }}>{data.case.explanation.summary || data.fused_explanation}</div>
          <ul style={{ paddingLeft: 18, marginBottom: 8 }}>
            {(data.case.explanation.factors ?? []).map((factor, i) => <li key={i} style={{ fontSize: 13, color: "#475569" }}>{factor}</li>)}
          </ul>
          <div style={{ fontSize: 13, color: "#92400e" }}><b>Uncertainty:</b> {data.case.explanation.uncertainty}</div>
          <div style={{ fontSize: 13, color: "#334155", marginTop: 6 }}><b>Generated next step:</b> {data.case.explanation.recommended_next_step}</div>
          <div style={{ fontSize: 13, color: "#334155", marginTop: 6 }}><b>Safe recommendations:</b> {(data.case.explanation.safe_recommendations ?? []).join(" · ")}</div>
          <div style={{ fontSize: 12, color: "#64748b", marginTop: 8, fontStyle: "italic" }}>{data.case.explanation.disclaimer}</div>
          {data.case.explanation_status === "fallback" && (
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>Deterministic fallback active; case review remains fully available.</div>
          )}
          {data.case.latest_explanation_call && <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>
            Server call #{data.case.latest_explanation_call.id} · {data.case.latest_explanation_call.endpoint} · {Math.round(data.case.latest_explanation_call.latency_ms)}ms · key retained on backend only
          </div>}
        </Card>
      )}

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
              triage signal — requires human review
            </div>
          </div>
        </div>
        <div style={{ marginTop: 10, fontSize: 12, color: "#64748b" }}>
          Owner: <b>{data.owner_label}</b> · Initial trigger: <b>{data.initial_owner}</b>
        </div>
      </Card>

      {data.case?.contacts && canCoordinateOperations && (
        <Card style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>Operations coordination</h3>
          <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
            Assigned to <b>{data.case.assigned_to}</b> · all contacts are synthetic demo details.
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 8, marginTop: 10 }}>
            {Object.entries(data.case.contacts).map(([key, contact]) => (
              <div key={key} style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 10 }}>
                <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase" }}>{contact.label}</div>
                <div style={{ fontWeight: 700, marginTop: 3 }}>{contact.name}</div>
                <div style={{ fontSize: 12, color: "#475569" }}>{contact.phone}</div>
              </div>
            ))}
          </div>
          {data.case.owner_role === "ops" && !["escalated", "closed"].includes(data.case.state) && (
            <div style={{ marginTop: 12, borderTop: "1px solid #e5e7eb", paddingTop: 10 }}>
              <textarea value={coordinationComment} onChange={e => setCoordinationComment(e.target.value)} maxLength={2000} rows={2} placeholder="Required coordination comment" style={{ width: "100%", boxSizing: "border-box", border: "1px solid #cbd5e1", borderRadius: 6, padding: 8, fontFamily: "inherit" }} />
              <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 7 }}>
                {[
                  ["notify_agent", "Notify agent"],
                  ["notify_field_officer", "Notify field officer"],
                  ["request_field_verification", "Request field verification"],
                  ["request_agent_confirmation", "Request agent confirmation"],
                  ["notify_area_manager", "Notify area manager"],
                  ["request_provider_operations_support", "Request provider operations support"],
                ].map(([action, label]) => <button key={action} disabled={!coordinationComment.trim() || !!coordinationBusy} onClick={() => coordinate(action)} style={coordBtn(!!coordinationComment.trim())}>{coordinationBusy === action ? "…" : label}</button>)}
              </div>
              <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 7 }}>
                <button disabled={!coordinationComment.trim() || !!coordinationBusy} onClick={() => coordinate("reassign", "field_officer")} style={coordBtn(!!coordinationComment.trim())}>Assign field officer</button>
                <button disabled={!coordinationComment.trim() || !!coordinationBusy} onClick={() => coordinate("reassign", "area_manager")} style={coordBtn(!!coordinationComment.trim())}>Reassign area manager</button>
                <button disabled={!coordinationComment.trim() || !!coordinationBusy} onClick={() => coordinate("reassign", "operations")} style={coordBtn(!!coordinationComment.trim())}>Return to operations queue</button>
              </div>
            </div>
          )}
        </Card>
      )}

      {role === "risk" && data.case && ["escalated", "risk_review"].includes(data.case.state) && (
        <Card style={{ marginBottom: 12, borderColor: "#c4b5fd", background: "#faf8ff" }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>Risk analyst investigation recommendation</h3>
          <p style={{ fontSize: 12, color: "#5b21b6", margin: "5px 0 10px" }}>
            Advisory review only. This recommendation does not determine wrongdoing and cannot close or financially affect the case.
          </p>
          {data.case.risk_recommendation && (
            <div style={{ padding: 10, borderRadius: 7, background: "#ede9fe", fontSize: 13, marginBottom: 10 }}>
              <b>{riskLabel(data.case.risk_recommendation)}</b> · {data.case.risk_recommendation_note}<br />
              <span style={{ fontSize: 11, color: "#6d28d9" }}>{data.case.risk_recommended_by} · {data.case.risk_recommended_at ? new Date(data.case.risk_recommended_at).toLocaleString() : "—"}</span>
            </div>
          )}
          <select value={riskRecommendation} onChange={e => setRiskRecommendation(e.target.value)} style={{ border: "1px solid #c4b5fd", borderRadius: 6, padding: 8, marginRight: 8 }}>
            <option value="requires_human_review">Requires Human Review</option>
            <option value="requires_further_investigation">Requires Further Investigation</option>
            <option value="return_to_operations">Return to Operations for Additional Context</option>
          </select>
          <textarea value={riskComment} onChange={e => setRiskComment(e.target.value)} maxLength={2000} rows={3} placeholder="Required investigation rationale" style={{ width: "100%", boxSizing: "border-box", border: "1px solid #c4b5fd", borderRadius: 6, padding: 8, fontFamily: "inherit", marginTop: 8 }} />
          <button disabled={reviewBusy || !riskComment.trim()} onClick={saveRiskRecommendation} style={{ marginTop: 6, border: 0, borderRadius: 6, padding: "8px 12px", background: riskComment.trim() ? "#6d28d9" : "#e5e7eb", color: riskComment.trim() ? "#fff" : "#94a3b8" }}>Record advisory recommendation</button>
          {reviewError && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 5 }}>{reviewError}</div>}
        </Card>
      )}

      <Card style={{ marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>Why this was flagged — and why it may still be benign</h3>
        <div style={{ marginTop: 6, fontSize: 12, color: "#475569", lineHeight: 1.5 }}>
          Every reason and evidence line below is <b>supporting context</b> for your
          review. The model surfaces patterns it cannot prove intent for — it is
          not a determination and not proof of wrongdoing. False positives
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
              {h.owner_from !== h.owner_to && <span> · owner: {h.owner_from || "unassigned"} → <b>{h.owner_to}</b></span>}
            </div>
          ))}
          <h4 style={{ fontSize: 13, marginTop: 10 }}>Notes</h4>
          {data.case.notes.map((n, i) => (
            <div key={i} style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>
              <b>{new Date(n.ts).toLocaleString()}</b> · {n.user}: {n.text}
            </div>
          ))}
          {["agent", "ops", "risk", "provider"].includes(role) && <div style={{ marginTop: 12, borderTop: "1px solid #e5e7eb", paddingTop: 10 }}>
            <textarea value={reviewNote} onChange={e => setReviewNote(e.target.value)} maxLength={2000} rows={2} placeholder="Add reviewer context or evidence note" style={{ width: "100%", boxSizing: "border-box", border: "1px solid #cbd5e1", borderRadius: 6, padding: 8, fontFamily: "inherit" }} />
            <button disabled={reviewBusy || !reviewNote.trim()} onClick={saveReviewNote} style={{ marginTop: 6, border: 0, borderRadius: 6, padding: "7px 12px", background: reviewNote.trim() ? "#0f172a" : "#e5e7eb", color: reviewNote.trim() ? "#fff" : "#94a3b8" }}>Add review note</button>
            {reviewError && <div style={{ color: "#dc2626", fontSize: 12, marginTop: 5 }}>{reviewError}</div>}
          </div>}
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
              <b>Audit record.</b> Every permitted action is timestamped in the
              case history. Operations coordinates service support and may
              escalate unusual activity. Risk analysts may add investigation
              notes and advisory recommendations only; they cannot determine
              wrongdoing or close the review. Add a note explaining each action.
            </div>          </Card>
        </RoleGuard>
        {role !== "risk" && (
          <div style={{ marginTop: 12, fontSize: 12, color: "#94a3b8" }}>
            Escalation forwards the evidence to Risk for advisory human review and further-investigation recommendations.
          </div>
        )}
      </>
    );
}

function coordBtn(active: boolean): React.CSSProperties {
  return { border: 0, borderRadius: 6, padding: "7px 10px", fontSize: 12, fontWeight: 600, background: active ? "#0369a1" : "#e5e7eb", color: active ? "#fff" : "#94a3b8", cursor: active ? "pointer" : "not-allowed" };
}

function riskLabel(value: string): string {
  return ({ requires_human_review: "Requires Human Review", requires_further_investigation: "Requires Further Investigation", return_to_operations: "Return to Operations for Additional Context" } as Record<string, string>)[value] ?? value;
}
