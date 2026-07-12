"use client";
import React, { useState } from "react";
import { AlertDetail } from "../lib/types";
import { client } from "../lib/client";
import { Card, Confidence, SeverityPill, StatusPill } from "./Primitives";

const HEALTH_BG: Record<string, string> = {
  normal: "#dcfce7", low: "#fef9c3", high: "#ffedd5", critical: "#fee2e2", unknown: "#e5e7eb",
};
const HEALTH_FG: Record<string, string> = {
  normal: "#166534", low: "#854d0e", high: "#9a3412", critical: "#991b1b", unknown: "#374151",
};
type Lang = "en" | "bn" | "banglish";

export function AlertCard({
  alert,
  onClick,
  onExplanationRegenerated,
  canRegenerateExplanation = true,
}: {
  alert: AlertDetail | import("../lib/types").DashboardAlert | undefined | null;
  onClick?: () => void;
  onExplanationRegenerated?: () => void;
  canRegenerateExplanation?: boolean;
}) {
  const [lang, setLang] = useState<Lang>("en");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!alert) return null;
  const a = alert as AlertDetail;
  const hasCase = (a as any).case !== undefined;
  const showLangSwitch = hasCase && canRegenerateExplanation && (a as any).id != null;

  async function regenerateExplanation(e: React.MouseEvent) {
    e.stopPropagation();
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await client.regenerateExplanation((a as any).id as number, lang);
      onExplanationRegenerated?.();
    } catch (ex: any) {
      setErr(String(ex?.message || ex));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card style={{ cursor: onClick ? "pointer" : "default" }}>
      <div onClick={onClick} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6, flexWrap: "wrap" }}>
            <SeverityPill severity={a.severity} />
            <StatusPill status={a.status} />
            {a.provider && <span style={{ fontSize: 12, color: "#64748b" }}>{a.provider}</span>}
            <span style={{ fontSize: 12, color: "#94a3b8" }}>·</span>
            <Confidence value={a.confidence} />
            <span style={{ fontSize: 12, color: "#94a3b8" }}>·</span>
            <span style={{ fontSize: 12, color: "#64748b" }}>priority {a.priority_score}/100</span>
          </div>
          <div style={{ fontSize: 15, fontWeight: 600, color: "#0f172a" }}>{a.title}</div>
          {("summary" in a) && (
            <div style={{ fontSize: 13, color: "#475569", marginTop: 4, lineHeight: 1.4 }}>
              {a.summary}
            </div>
          )}
          {("fused_explanation" in a) && (
            <div style={{ fontSize: 13, color: "#475569", marginTop: 4, lineHeight: 1.4 }}>
              {a.fused_explanation}
            </div>
          )}
        </div>
        {showLangSwitch && (
          <div
            onClick={e => e.stopPropagation()}
            style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-end", minWidth: 132 }}
          >
            <select
              value={lang}
              onChange={e => setLang(e.target.value as Lang)}
              aria-label="Explanation language"
              style={{ border: "1px solid #7dd3fc", borderRadius: 6, padding: "4px 6px", fontSize: 12, background: "#fff", width: "100%" }}
            >
              <option value="en">English</option>
              <option value="bn">বাংলা</option>
              <option value="banglish">Banglish</option>
            </select>
            <button
              type="button"
              disabled={busy}
              onClick={regenerateExplanation}
              style={{ border: "1px solid #7dd3fc", background: "#fff", color: "#0369a1", borderRadius: 6, padding: "4px 8px", fontSize: 12, cursor: busy ? "wait" : "pointer", width: "100%" }}
            >
              {busy ? "Generating…" : "Regenerate explanation"}
            </button>
            {err && <div style={{ fontSize: 11, color: "#dc2626", maxWidth: 132 }}>{err}</div>}
          </div>
        )}
      </div>
    </Card>
  );
}

export function HealthBadge({ health }: { health: string }) {
  const bg = HEALTH_BG[health] || HEALTH_BG.unknown;
  const fg = HEALTH_FG[health] || HEALTH_FG.unknown;
  return (
    <span style={{ background: bg, color: fg, padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 600 }}>
      {health}
    </span>
  );
}