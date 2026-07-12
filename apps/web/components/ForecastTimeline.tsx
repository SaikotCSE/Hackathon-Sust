"use client";
import React from "react";

/**
 * ForecastTimeline — minimal "Projected Service Pressure" block.
 *
 * Single intent: answer "when does this provider run out?" in one glance.
 *
 *   • One line: phase + ETA (the answer)
 *   • One thin strip: how urgent that is, visually
 *   • Two pills: time-to-shortage, burn rate (the numbers behind the answer)
 *   • Tiny basis note: one line, why we believe it
 *
 * Confidence is shown beside every estimate so uncertain data cannot look
 * equivalent to a well-supported trend.
 *
 * No fetching. Pure presentational.
 */
export interface ForecastTimelineProps {
  providerKey: string;        // "physical" | "bkash" | "nagad" | "rocket"
  burnRatePerMin: number;
  hoursToShortage: number | null;
  confidence: number;
  forecastSummary?: string;   // curated one-line basis from the backend (preferred)
  forecastReasons?: string[]; // technical/audit trail; fallback when summary is absent
  history?: number[];
  degraded?: boolean;
  degradedReason?: string | null;
  forecastState?: string;
  forecastAgeMinutes?: number | null;
  projectedBalance8h?: number | null;
}

const PHASE = {
  safe:     { fg: "#16a34a", bg: "#dcfce7" },
  watch:    { fg: "#ca8a04", bg: "#fef9c3" },
  critical: { fg: "#ea580c", bg: "#ffedd5" },
  depleted: { fg: "#dc2626", bg: "#fee2e2" },
};

function phaseForHours(h: number | null): keyof typeof PHASE {
  if (h == null) return "watch";
  if (h <= 0)    return "depleted";
  if (h < 2)     return "critical";
  if (h < 8)     return "watch";
  return "safe";
}

function fmtDuration(hours: number | null): string {
  if (hours == null || !isFinite(hours)) return "—";
  if (hours < 0) return "now";
  const totalMin = Math.round(hours * 60);
  if (totalMin < 1) return "<1 min";
  if (totalMin < 60) return `${totalMin} min`;
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h < 24) return m ? `${h}h ${m}m` : `${h}h`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return rh ? `${d}d ${rh}h` : `${d}d`;
}

function fmtBurn(rate: number): string {
  if (!rate || rate <= 0) return "৳0/min";
  if (rate >= 1000) return `৳${(rate / 1000).toFixed(rate >= 10_000 ? 0 : 1)}k/min`;
  return `৳${Math.round(rate)}/min`;
}

export function ForecastTimeline({
  providerKey,
  burnRatePerMin,
  hoursToShortage,
  confidence,
  forecastSummary,
  forecastReasons = [],
  history = [],
  degraded = false,
  degradedReason = null,
  forecastState,
  forecastAgeMinutes,
  projectedBalance8h,
}: ForecastTimelineProps) {
  const phase: keyof typeof PHASE = degraded ? "watch" : phaseForHours(hoursToShortage);
  const phaseInfo = PHASE[phase];

  // Prefer the curated summary (single human-readable basis line). Fall back
  // to the first technical reason; finally to a generic phrasing.
  const basis =
    (forecastSummary && forecastSummary.trim()) ||
    (forecastReasons && forecastReasons.length > 0 ? forecastReasons[0] : "") ||
    (providerKey === "physical"
      ? `cash drawer drawdown over ~${Math.max(1, Math.round((history?.length ?? 0) / 2))}h`
      : `last ${Math.max(1, history?.length ?? 0)} intervals' avg outflow`);

  const runway = degraded
    ? "Forecast unavailable"
    : forecastState === "low_confidence"
      ? "Estimate unavailable"
    : forecastState === "stable"
      ? "No depletion trend"
      : hoursToShortage == null
        ? "No shortage projected"
        : fmtDuration(hoursToShortage);
  const statusLabel = degraded ? "Needs fresh data" : forecastState === "low_confidence" ? "Low confidence" : phase === "safe" ? "Healthy runway" : phase;
  return <div>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
      <span style={{ color: phaseInfo.fg, background: phaseInfo.bg, borderRadius: 999, padding: "4px 9px", fontSize: 11, fontWeight: 800, textTransform: "uppercase" }}>{statusLabel}</span>
      <span style={{ fontSize: 18, fontWeight: 750, color: degraded ? "#92400e" : phaseInfo.fg }}>{runway}</span>
    </div>
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginTop: 10 }}>
      <div><div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase" }}>Net burn</div><b style={{ fontSize: 13 }}>{fmtBurn(burnRatePerMin)}</b></div>
      <div><div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase" }}>Confidence</div><b style={{ fontSize: 13, color: confidence < .5 ? "#b45309" : "#0f172a" }}>{Math.round(confidence * 100)}%</b></div>
      <div><div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase" }}>8h balance</div><b style={{ fontSize: 13 }}>{projectedBalance8h == null ? "—" : `৳${Math.round(projectedBalance8h).toLocaleString()}`}</b></div>
    </div>
    <div style={{ fontSize: 12, color: degraded ? "#92400e" : "#64748b", marginTop: 9, lineHeight: 1.4 }}>{degraded ? degradedReason : basis}</div>
    <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>{forecastAgeMinutes == null ? "Not generated" : `Updated ${forecastAgeMinutes < 1 ? "just now" : `${Math.round(forecastAgeMinutes)} min ago`}`}</div>
  </div>;
}
