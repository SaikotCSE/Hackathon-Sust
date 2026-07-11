"use client";
import React from "react";
import type { DashboardProvider } from "../lib/types";
import { Card } from "./Primitives";

const PROVIDER_STYLE: Record<string, { dot: string; title: string; subtitle: string; short: string }> = {
  physical: { dot: "#1e293b", title: "Physical Cash",  subtitle: "Shared drawer for this outlet", short: "Physical" },
  bkash:    { dot: "#e11d48", title: "bKash",          subtitle: "Mobile wallet — bKash",        short: "bKash" },
  nagad:    { dot: "#ea580c", title: "Nagad",          subtitle: "Mobile wallet — Nagad",        short: "Nagad" },
  rocket:   { dot: "#7c3aed", title: "Rocket",         subtitle: "Mobile wallet — Rocket",       short: "Rocket" },
};

const HEALTH_BG: Record<string, string> = {
  normal: "#dcfce7", low: "#fef9c3", medium: "#ffedd5", high: "#ffedd5", critical: "#fee2e2", unknown: "#e5e7eb",
};
const HEALTH_FG: Record<string, string> = {
  normal: "#166534", low: "#854d0e", medium: "#9a3412", high: "#9a3412", critical: "#991b1b", unknown: "#374151",
};

function fmtBDT(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1000) return `৳${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  return `৳${n.toFixed(0)}`;
}

function fmtTiny(n: number): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return n.toFixed(0);
}

function tier(hours: number | null): { label: string; color: string } {
  if (hours == null) return { label: "no projection", color: "#64748b" };
  if (hours < 0.5) return { label: "Critical", color: "#dc2626" };
  if (hours < 2)   return { label: "High",     color: "#ea580c" };
  if (hours < 6)   return { label: "Medium",   color: "#ca8a04" };
  return                   { label: "Low",      color: "#16a34a" };
}

function demandLabelColor(label?: string): { bg: string; fg: string } {
  switch (label) {
    case "high":   return { bg: "#fee2e2", fg: "#991b1b" };
    case "medium": return { bg: "#ffedd5", fg: "#9a3412" };
    default:       return { bg: "#dcfce7", fg: "#166534" };
  }
}

export function ProviderLiquidityCard({ p }: { p: DashboardProvider }) {
  const meta = PROVIDER_STYLE[p.provider] ?? {
    dot: "#64748b", title: p.provider, subtitle: "Mobile wallet", short: p.provider,
  };
  const tierForHealth = tier(p.hours_to_shortage);
  const balBg = p.balance == null ? "#f3f4f6" :
    p.balance <= 0 ? "#fee2e2" :
    p.health === "critical" ? "#fee2e2" :
    p.health === "high"     ? "#ffedd5" :
    p.health === "low"      ? "#fef9c3" :
                              "#fff";
  const conf = Math.round((p.forecast_confidence ?? 0) * 100);
  const confColor = conf >= 80 ? "#16a34a" : conf >= 55 ? "#ca8a04" : "#dc2626";
  const demand = demandLabelColor(p.current_demand_label);

  const history = p.history ?? [];
  const isWalled = p.balance == null && (p.history ?? []).length === 0;

  return (
    <Card style={{
      background: balBg,
      borderColor: p.health === "critical" ? "#fecaca" : p.health === "high" ? "#fed7aa" : "#e5e7eb",
      position: "relative",
    }}>
      {/* Header row */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 10, height: 10, borderRadius: 999, background: meta.dot, display: "inline-block" }} />
          <div style={{ fontWeight: 700, fontSize: 14 }}>{meta.title}</div>
        </div>
        <span style={{
          background: HEALTH_BG[p.health] ?? HEALTH_BG.unknown,
          color: HEALTH_FG[p.health] ?? HEALTH_FG.unknown,
          padding: "3px 10px", borderRadius: 999, fontSize: 11, fontWeight: 700,
        }}>{tierForHealth.label}</span>
      </div>
      {/* Sub-line */}
      <div style={{ fontSize: 12, color: "#64748b", marginTop: 4 }}>
        {p.data_quality < 0.7
          ? <span style={{ color: "#dc2626" }}>Data quality degraded ({Math.round(p.data_quality * 100)}%)</span>
          : "Data quality: ok"}
        {p.forecast_reasons?.[0] && (
          <span style={{ marginLeft: 6, color: "#94a3b8" }}>
            · {p.forecast_reasons[0]}
          </span>
        )}
      </div>

      {/* Big balance */}
      <div style={{
        fontSize: 28, fontWeight: 800, marginTop: 10,
        color: p.balance == null ? "#94a3b8" : p.balance <= 0 ? "#dc2626" : "#0f172a",
      }}>
        {fmtBDT(p.balance)}
      </div>

      {/* Sparkline */}
      {!isWalled && history.length > 1 && (
        <svg width="100%" height="36" viewBox={`0 0 ${history.length * 12} 36`} preserveAspectRatio="none" style={{ marginTop: 6 }}>
          {(() => {
            const max = Math.max(...history, 1);
            const min = Math.min(...history, 0);
            const range = Math.max(max - min, 1);
            const pts = history.map((b, i) => {
              const x = i * 12;
              const y = 34 - ((b - min) / range) * 32;
              return `${x},${y}`;
            }).join(" ");
            return <>
              <polyline points={pts} fill="none" stroke={meta.dot} strokeWidth="2" />
              {/* last-point dot */}
              {(() => {
                const last = history[history.length - 1];
                const x = (history.length - 1) * 12;
                const y = 34 - ((last - min) / range) * 32;
                return <circle cx={x} cy={y} r="3" fill={meta.dot} />;
              })()}
            </>;
          })()}
        </svg>
      )}

      {/* Recent deltas line */}
      <div style={{ fontSize: 12, color: "#475569", marginTop: 8, display: "flex", gap: 12, flexWrap: "wrap" }}>
        <span style={{ color: "#94a3b8" }}>Recent:</span>
        {isWalled
          ? <span style={{ fontStyle: "italic", color: "#94a3b8" }}>walled for this role</span>
          : (p.recent_deltas?.length
              ? p.recent_deltas!.map((d, i) => (
                  <span key={i} style={{ color: d < 0 ? "#dc2626" : "#16a34a", fontVariantNumeric: "tabular-nums" }}>
                    {d < 0 ? "↓" : "↑"} ৳{fmtTiny(d)}
                  </span>
                ))
              : <span style={{ color: "#94a3b8" }}>—</span>)}
      </div>

      {/* Demand block */}
      <div style={{ marginTop: 12, fontSize: 12, color: "#0f172a" }}>
        <div style={{ color: "#94a3b8", fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>
          Provider-aware demand
        </div>
        <div style={{ marginTop: 4, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span style={{ fontSize: 13 }}>
            Current demand: <b style={{ color: demand.fg }}>{(p.current_demand_label ?? "low").toUpperCase()}</b>
          </span>
          <span style={{ color: "#94a3b8" }}>·</span>
          <span style={{ fontSize: 13 }}>
            Next few hours: <b>{p.expected_outflow_next_hours != null ? `~৳${fmtTiny(p.expected_outflow_next_hours)} expected provider outflow` : "—"}</b>
          </span>
        </div>
      </div>

      {/* Shortage */}
      <div style={{ marginTop: 10, fontSize: 12, color: "#0f172a" }}>
        <div style={{ color: "#94a3b8", fontSize: 11, textTransform: "uppercase", letterSpacing: 1 }}>
          Projected service pressure
        </div>
        <div style={{ marginTop: 4, fontSize: 13 }}>
          Estimated cash shortage in <b style={{ color: tierForHealth.color }}>{p.shortage_eta_human ?? "—"}</b>
        </div>
      </div>

      {/* Confidence */}
      <div style={{ marginTop: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#64748b" }}>
          <span>Confidence score</span>
          <span style={{ color: confColor, fontWeight: 700 }}>{conf}%</span>
        </div>
        <div style={{ height: 6, background: "#e5e7eb", borderRadius: 999, marginTop: 4, overflow: "hidden" }}>
          <div style={{ width: `${conf}%`, height: "100%", background: confColor }} />
        </div>
        <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>
          Confidence reflects recent trend stability and {p.provider === "physical" ? "shared cash drawer" : "provider"} data quality.
        </div>
      </div>
    </Card>
  );
}