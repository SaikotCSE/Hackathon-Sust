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
  if (Math.abs(n) >= 1000) return `৳${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  return `৳${n.toFixed(2)}`;
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

export function ProviderLiquidityCard({
  p,
  chart,
}: {
  p: DashboardProvider;
  /** Optional line-chart slot rendered just under the big balance. */
  chart?: React.ReactNode;
}) {
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
  const isWalled = p.balance == null && history.length === 0;
    const degraded = !!p.degraded;
    // Server-supplied eta text already says "feed stale — projection paused"
    // when degraded, so the projected-pressure block renders that explicitly.
    const etaText = p.shortage_eta_human ?? (degraded ? "feed degraded — wait for data" : "—");

    return (
      <Card style={{
        background: balBg,
        borderColor: p.health === "critical" ? "#fecaca" : p.health === "high" ? "#fed7aa" : "#e5e7eb",
        position: "relative",
        padding: 18,
      }}>
        {/* Header row */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ width: 14, height: 14, borderRadius: 999, background: meta.dot, display: "inline-block" }} />
            <div style={{ fontWeight: 700, fontSize: 20 }}>{meta.title}</div>
          </div>
          <span style={{
            background: HEALTH_BG[p.health] ?? HEALTH_BG.unknown,
            color: HEALTH_FG[p.health] ?? HEALTH_FG.unknown,
            padding: "5px 14px", borderRadius: 999, fontSize: 14, fontWeight: 700,
          }}>{tierForHealth.label}</span>
        </div>

        {/* Degraded-state badge: explicit, server-driven. Renders above the
            data quality line so it's the first thing the eye catches when
            this provider's feed is bad. */}
        {degraded && (
          <div style={{
            marginTop: 10,
            padding: "6px 10px",
            background: "#fef3c7",
            color: "#92400e",
            border: "1px solid #fcd34d",
            borderRadius: 8,
            fontSize: 13,
            fontWeight: 600,
          }}>
            ⚠ Feed degraded — projection paused
            {p.degraded_reason ? <span style={{ fontWeight: 400, marginLeft: 6 }}>({p.degraded_reason})</span> : null}
          </div>
        )}
      <div style={{ fontSize: 14, color: "#64748b", marginTop: 8 }}>
        {p.data_quality < 0.7
          ? <span style={{ color: "#dc2626", fontWeight: 600 }}>Data quality degraded ({Math.round(p.data_quality * 100)}%)</span>
          : "Data Quality: Ok"}
      </div>

      {/* Big balance — true headline of the card */}
      <div style={{
        fontSize: 44, fontWeight: 800, marginTop: 12, lineHeight: 1.05, letterSpacing: -0.5,
        color: p.balance == null ? "#94a3b8" : p.balance <= 0 ? "#dc2626" : "#0f172a",
      }}>
        {fmtBDT(p.balance)}
      </div>

      {/* Per-card time-series chart — sits right under the amount so the
          eye reads amount → trend → projection in one continuous block. */}
      {chart && (
        <div style={{
          marginTop: 14,
          marginLeft: -6,
          marginRight: -6,
        }}>
          {chart}
        </div>
      )}

      {/* Recent deltas line */}
      <div style={{ fontSize: 14, color: "#475569", marginTop: 14, display: "flex", gap: 12, flexWrap: "wrap" }}>
        <span style={{ color: "#94a3b8" }}>Recent:</span>
        {isWalled
          ? <span style={{ fontStyle: "italic", color: "#94a3b8" }}>walled for this role</span>
          : (p.recent_deltas?.length
              ? p.recent_deltas!.map((d, i) => (
                  <span key={i} style={{ color: d < 0 ? "#dc2626" : "#16a34a", fontVariantNumeric: "tabular-nums" }}>
                    {d < 0 ? "↓" : "↑"}৳{fmtTiny(Math.abs(d))}
                  </span>
                ))
              : <span style={{ color: "#94a3b8" }}>—</span>)}
      </div>

      {/* Demand block */}
      <div style={{ marginTop: 18, fontSize: 14, color: "#0f172a" }}>
        <div style={{ color: "#64748b", fontSize: 12, textTransform: "uppercase", letterSpacing: 1, fontWeight: 600 }}>
          Provider-Aware Demand
        </div>
        <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span style={{ fontSize: 16 }}>
            Current demand: <b style={{ color: demand.fg }}>{(p.current_demand_label ?? "low").replace(/^./, c => c.toUpperCase())}</b>
          </span>
        </div>
        <div style={{ fontSize: 16, marginTop: 6 }}>
          Next few hours: <b>{p.expected_outflow_next_hours != null ? `~৳${fmtTiny(p.expected_outflow_next_hours)} expected ${p.provider === "physical" ? "cash" : "provider"} outflow` : "—"}</b>
        </div>
      </div>

      {/* Projected service pressure — forecast lives here, per card */}
      <div style={{ marginTop: 16, fontSize: 14, color: "#0f172a" }}>
        <div style={{ color: "#64748b", fontSize: 12, textTransform: "uppercase", letterSpacing: 1, fontWeight: 600 }}>
          Projected Service Pressure
        </div>
        <div style={{ marginTop: 8, fontSize: 16 }}>
            Estimated {p.provider === "physical" ? "cash" : `${meta.short} balance`} shortage in{" "}
            <b style={{ color: degraded ? "#92400e" : tierForHealth.color }}>{etaText}</b>.
          </div>
        <div style={{ fontSize: 13, color: "#64748b", marginTop: 6 }}>
          based on {p.provider === "physical"
            ? `shared cash drawer drawdown over ~${Math.max(1, Math.round(history.length / 2))} hours`
            : `the last ${Math.max(1, history.length)} intervals' average outflow rate`}
        </div>
      </div>

      {/* Confidence */}
      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 14, color: "#475569", fontWeight: 600 }}>
          <span>Confidence Score</span>
          <span style={{ color: confColor, fontWeight: 700 }}>{conf}%</span>
        </div>
        <div style={{ height: 10, background: "#e5e7eb", borderRadius: 999, marginTop: 8, overflow: "hidden" }}>
          <div style={{ width: `${conf}%`, height: "100%", background: confColor }} />
        </div>
        <div style={{ fontSize: 13, color: "#64748b", marginTop: 8 }}>
          Confidence reflects recent trend stability and {p.provider === "physical" ? "shared cash drawer" : "provider"} data quality.
        </div>
      </div>
    </Card>
  );
}