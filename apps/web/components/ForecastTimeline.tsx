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
 * Confidence is intentionally NOT shown. It's the model's own belief about
 * itself, not a fact about the world, and dressing it up as a verdict made
 * the card look worse. The backend can surface it once the logic is fixed;
 * the UI stays out of the way until then.
 *
 * No fetching. Pure presentational.
 */
export interface ForecastTimelineProps {
  providerKey: string;        // "physical" | "bkash" | "nagad" | "rocket"
  burnRatePerMin: number;
  hoursToShortage: number | null;
  forecastSummary?: string;   // curated one-line basis from the backend (preferred)
  forecastReasons?: string[]; // technical/audit trail; fallback when summary is absent
  history?: number[];
  degraded?: boolean;
  degradedReason?: string | null;
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

/** Position of the "now" marker along the 0..1 strip. */
function urgencyT(hours: number | null): number {
  if (hours == null) return 0.15;
  return Math.min(Math.max(hours, 0), 72) / 72;
}

export function ForecastTimeline({
  providerKey,
  burnRatePerMin,
  hoursToShortage,
  forecastSummary,
  forecastReasons = [],
  history = [],
  degraded = false,
  degradedReason = null,
}: ForecastTimelineProps) {
  const phase: keyof typeof PHASE = degraded ? "watch" : phaseForHours(hoursToShortage);
  const phaseInfo = PHASE[phase];
  const t = urgencyT(hoursToShortage);

  // Prefer the curated summary (single human-readable basis line). Fall back
  // to the first technical reason; finally to a generic phrasing.
  const basis =
    (forecastSummary && forecastSummary.trim()) ||
    (forecastReasons && forecastReasons.length > 0 ? forecastReasons[0] : "") ||
    (providerKey === "physical"
      ? `cash drawer drawdown over ~${Math.max(1, Math.round((history?.length ?? 0) / 2))}h`
      : `last ${Math.max(1, history?.length ?? 0)} intervals' avg outflow`);

  return (
    <div>
      {/* Answer */}
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 8,
          marginBottom: 8,
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: phaseInfo.fg,
              display: "inline-block",
            }}
          />
          <span style={{ fontSize: 13, fontWeight: 700, color: phaseInfo.fg, textTransform: "uppercase", letterSpacing: 0.5 }}>
            {degraded ? "Paused" : phaseForHours(hoursToShortage)}
          </span>
        </span>
        <span style={{ fontSize: 15, color: "#0f172a", fontVariantNumeric: "tabular-nums" }}>
          {degraded ? (
            <span style={{ color: "#92400e" }}>{degradedReason ?? "feed degraded"}</span>
          ) : (
            <>
              shortage in <b style={{ color: phaseInfo.fg }}>{fmtDuration(hoursToShortage)}</b>
            </>
          )}
        </span>
      </div>

      {/* Strip */}
      <div
        style={{
          position: "relative",
          height: 8,
          borderRadius: 999,
          overflow: "hidden",
          display: "flex",
          border: "1px solid #e2e8f0",
        }}
      >
        <div style={{ flex: 5, background: PHASE.safe.bg }} />
        <div style={{ flex: 2, background: PHASE.watch.bg }} />
        <div style={{ flex: 1.5, background: PHASE.critical.bg }} />
        <div style={{ flex: 0.6, background: PHASE.depleted.bg }} />
        <div
          style={{
            position: "absolute",
            left: `calc(${t * 100}% - 1px)`,
            top: -3,
            bottom: -3,
            width: 2,
            background: "#0f172a",
          }}
        />
      </div>

      {/* Two numbers + one line */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 14,
          marginTop: 10,
          fontSize: 13,
          color: "#475569",
        }}
      >
        <span style={{ fontVariantNumeric: "tabular-nums" }}>
          burn <b style={{ color: "#0f172a" }}>{fmtBurn(burnRatePerMin)}</b>
        </span>
        <span style={{ color: "#cbd5e1" }}>·</span>
        <span style={{ fontSize: 12, color: "#94a3b8" }}>based on {basis}</span>
      </div>
    </div>
  );
}