"use client";
import React from "react";
import type { Severity } from "../lib/types";

const SEV: Record<Severity, { bg: string; fg: string; label: string }> = {
  normal:   { bg: "#e0e7ff", fg: "#3730a3", label: "Normal" },
  low:      { bg: "#dcfce7", fg: "#166534", label: "Low" },
  high:     { bg: "#ffedd5", fg: "#9a3412", label: "High" },
  critical: { bg: "#fee2e2", fg: "#991b1b", label: "Critical" },
};

const SEV_ORDER: Severity[] = ["normal", "low", "high", "critical"];

export function SeverityPill({ severity }: { severity: Severity }) {
  const s = SEV_ORDER.includes(severity) ? SEV[severity] : SEV.normal;
  return (
    <span style={{ background: s.bg, color: s.fg, padding: "2px 8px", borderRadius: 999, fontSize: 12, fontWeight: 600 }}>
      {s.label}
    </span>
  );
}

const STATUS: Record<string, { bg: string; fg: string }> = {
  open:        { bg: "#e0e7ff", fg: "#3730a3" },
  assigned:    { bg: "#cffafe", fg: "#155e75" },
  acknowledged:{ bg: "#cffafe", fg: "#155e75" },
  under_review:{ bg: "#fef3c7", fg: "#92400e" },
  escalated:   { bg: "#ede9fe", fg: "#6d28d9" },
  compliance_decision: { bg: "#e0f2fe", fg: "#075985" },
  risk_review: { bg: "#ede9fe", fg: "#5b21b6" },
  resolved:    { bg: "#dcfce7", fg: "#166534" },
  closed:      { bg: "#e5e7eb", fg: "#374151" },
};

export function StatusPill({ status }: { status: string }) {
  const s = STATUS[status] || STATUS.open;
  return (
    <span style={{ background: s.bg, color: s.fg, padding: "2px 8px", borderRadius: 999, fontSize: 12, fontWeight: 500 }}>
      {status}
    </span>
  );
}

export function Confidence({ value, label }: { value: number; label?: string }) {
  const pct = Math.round(value * 100);
  // Approximate uncertainty band — confidence is the model's self-reported
  // certainty, not a determination. Show ±N% alongside so the reader can
  // see the uncertainty explicitly. The tooltip repeats this so any analyst
  // who hovers the pill gets the framing.
  const uncertainty = pct >= 80 ? 5 : pct >= 55 ? 10 : 20;
  const color = pct >= 80 ? "#16a34a" : pct >= 55 ? "#ca8a04" : "#dc2626";
  const tooltip = (label || "Confidence") +
    " — model certainty for triage only, not proof of wrongdoing. " +
    `False positives are expected (roughly ±${uncertainty}%).`;
  return (
    <span title={tooltip} style={{ fontSize: 12, color }}>
      ● {pct}% (±{uncertainty}%){label ? ` ${label}` : ""}
    </span>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: "#fff",
      border: "1px solid #e5e7eb",
      borderRadius: 12,
      padding: 18,
      boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
      ...style,
    }}>{children}</div>
  );
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 20 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: "#0f172a", letterSpacing: -0.3 }}>{title}</h1>
        {subtitle && <div style={{ color: "#64748b", marginTop: 6, fontSize: 14 }}>{subtitle}</div>}
      </div>
      <div>{right}</div>
    </div>
  );
}

export function Disclaimer() {
  return (
    <div style={{
      background: "#fef9c3", border: "1px solid #fde047", color: "#713f12",
      borderRadius: 8, padding: "10px 14px", fontSize: 14, marginBottom: 14,
    }}>
      ⚠ Advisory only. Unusual signals require human review. No transactions or account actions are executed.
    </div>
  );
}
