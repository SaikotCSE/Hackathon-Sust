"use client";
import React from "react";
import { AlertDetail } from "../lib/types";
import { Card, Confidence, SeverityPill, StatusPill } from "./Primitives";

const HEALTH_BG: Record<string, string> = {
  normal: "#dcfce7", low: "#fef9c3", high: "#ffedd5", critical: "#fee2e2", unknown: "#e5e7eb",
};
const HEALTH_FG: Record<string, string> = {
  normal: "#166534", low: "#854d0e", high: "#9a3412", critical: "#991b1b", unknown: "#374151",
};

export function AlertCard({ alert, onClick }: { alert: AlertDetail | import("../lib/types").DashboardAlert | undefined | null; onClick?: () => void }) {
  if (!alert) return null;
  const a = alert as AlertDetail;
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