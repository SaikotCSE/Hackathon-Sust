"use client";
import React, { useState } from "react";
import { client } from "../lib/client";
import { usePrincipal } from "./PrincipalProvider";
import { canPerformAction } from "../lib/rbac";
import type { AlertDetail } from "../lib/types";

const ACTION_STYLE: Record<string, { label: string; color: string; hint: string }> = {
  ack:      { label: "Acknowledge",       color: "#0ea5e9", hint: "I see this — taking ownership." },
  review:   { label: "Start review",      color: "#ca8a04", hint: "Open the case and start investigation." },
  resolve:  { label: "Resolve",           color: "#16a34a", hint: "Case handled — close out." },
  escalate: { label: "Escalate to risk",  color: "#7c3aed", hint: "Hands off to Risk / Compliance." },
  decision: { label: "Compliance decision", color: "#0891b2", hint: "Final compliance ruling." },
  close:    { label: "Close (compliance)", color: "#374151", hint: "Case closed — final." },
};

export function AlertActionStrip({
  alert,
  onChanged,
}: {
  alert: AlertDetail;
  onChanged: () => void;
}) {
  const { principal } = usePrincipal();
  const role = principal?.role;
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const actions = alert.recommended_actions ?? [];
  const transitionActions = ["ack", "review", "resolve", "escalate", "decision", "close"];

  async function go(action: string) {
    setBusy(action);
    setError(null);
    try {
      await client.transitionAlert(alert.id, action as any, note || undefined);
      setNote("");
      onChanged();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {actions.length > 0 && (
        <div>
          <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 1 }}>
            Suggested next moves (by weight)
          </div>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {actions.map((a, i) => (
              <li key={i} style={{ fontSize: 13, color: "#475569", marginBottom: 2 }}>
                <b>{a.label}</b> <span style={{ color: "#94a3b8" }}>(key: {a.key}, weight {a.weight})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <div style={{ fontSize: 11, color: "#64748b", textTransform: "uppercase", letterSpacing: 1 }}>
          Take action
        </div>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Note (optional, recorded in audit trail)"
          rows={2}
          style={{
            width: "100%", marginTop: 6, padding: 8, borderRadius: 6,
            border: "1px solid #cbd5e1", fontSize: 13, fontFamily: "inherit",
          }}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          {transitionActions.map(a => {
            const meta = ACTION_STYLE[a];
            const allowed = canPerformAction(role, a);
            return (
              <button
                key={a}
                onClick={() => go(a)}
                disabled={!allowed || busy === a}
                title={allowed ? meta.hint : `${role ?? "—"} cannot ${a}`}
                style={{
                  background: allowed ? meta.color : "#e5e7eb",
                  color: allowed ? "#fff" : "#94a3b8",
                  border: 0, borderRadius: 6,
                  padding: "6px 12px", fontSize: 13, fontWeight: 600,
                  cursor: allowed ? "pointer" : "not-allowed",
                  opacity: busy === a ? 0.7 : 1,
                }}
              >
                {busy === a ? "…" : meta.label}
                {!allowed && <span style={{ marginLeft: 6, fontSize: 10 }}>· restricted</span>}
              </button>
            );
          })}
        </div>
        {error && <div style={{ marginTop: 6, color: "#dc2626", fontSize: 12 }}>{error}</div>}
      </div>
    </div>
  );
}