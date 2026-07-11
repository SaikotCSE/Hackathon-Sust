"use client";
import React from "react";
import type { DashboardProvider } from "../lib/types";
import { Card } from "./Primitives";
import { ForecastTimeline } from "./ForecastTimeline";

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
  if (hours == null) return { label: "No ETA", color: "#64748b" };
  if (hours < 0.5) return { label: "Critical", color: "#dc2626" };
  if (hours < 2)   return { label: "High",     color: "#ea580c" };
  if (hours < 6)   return { label: "Medium",   color: "#ca8a04" };
  return                   { label: "Healthy",  color: "#16a34a" };
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
  const tierForHealth = p.degraded ? { label: "Data needed", color: "#92400e" }
    : p.forecast_state === "stable" ? { label: "Stable", color: "#16a34a" }
    : p.forecast_state === "low_confidence" ? { label: "Low confidence", color: "#b45309" }
    : tier(p.hours_to_shortage);
  const balBg = p.balance == null ? "#f3f4f6" :
    p.balance <= 0 ? "#fee2e2" :
    p.health === "critical" ? "#fee2e2" :
    p.health === "high"     ? "#ffedd5" :
    p.health === "low"      ? "#fef9c3" :
                              "#fff";
  const demand = demandLabelColor(p.current_demand_label);

  const history = p.history ?? [];
    const degraded = !!p.degraded;
    const statusBg = degraded ? "#fef3c7" : p.forecast_state === "low_confidence" ? "#ffedd5" : HEALTH_BG[p.health] ?? HEALTH_BG.unknown;

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
            background: statusBg,
            color: tierForHealth.color,
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

      <div style={{ marginTop: 14, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <div style={{ background: "#f8fafc", padding: "9px 10px", borderRadius: 8 }}>
          <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase" }}>8h expected demand</div>
          <b style={{ fontSize: 14 }}>{p.expected_outflow_next_hours != null ? `৳${fmtTiny(p.expected_outflow_next_hours)}` : "—"}</b>
        </div>
        <div style={{ background: demand.bg, padding: "9px 10px", borderRadius: 8 }}>
          <div style={{ fontSize: 10, color: demand.fg, textTransform: "uppercase" }}>Demand pressure</div>
          <b style={{ fontSize: 14, color: demand.fg }}>{(p.current_demand_label ?? "low").replace(/^./, c => c.toUpperCase())}</b>
        </div>
      </div>

      {/* Projected service pressure — visual forecast lives here */}
      <div style={{ marginTop: 18 }}>
        <div
          style={{
            color: "#64748b",
            fontSize: 12,
            textTransform: "uppercase",
            letterSpacing: 1,
            fontWeight: 600,
            marginBottom: 6,
          }}
        >
          Projected Service Pressure
        </div>
        <ForecastTimeline
          providerKey={p.provider}
          burnRatePerMin={p.burn_rate_per_min}
          hoursToShortage={p.hours_to_shortage}
          confidence={p.forecast_confidence}
          forecastSummary={p.forecast_summary}
          forecastReasons={p.forecast_reasons}
          history={history}
          degraded={degraded}
          degradedReason={p.degraded_reason ?? null}
          forecastState={p.forecast_state}
          forecastAgeMinutes={p.forecast_age_minutes}
          projectedBalance8h={p.projected_balance_8h}
        />
      </div>
    </Card>
  );
}
