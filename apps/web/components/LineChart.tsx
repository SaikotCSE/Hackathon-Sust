"use client";
import React from "react";

/**
 * A small SVG line chart with real x/y axes, gridlines, and an optional
 * "projected burn-rate" dotted extrapolation to depletion. Hand-rolled so
 * we don't pull in a 200kb dep for one chart.
 *
 * X-axis is real time (derived from `timestamps`) — labels auto-pick the
 * best format based on the visible span:
 *   <  6h      → "HH:mm"
 *   <  48h     → "ddd HH:mm"   (e.g. "Mon 14:00")
 *   <  14d     → "MMM DD"      (e.g. "Jul 11")
 *   >= 14d     → "MMM DD"      plus a "Xw ago" hint
 *
 * Y-axis is always ৳ BDT amount only (no rotated stray unit text — labels
 * are inline next to the tick).
 */
export interface LineChartProps {
  data: number[];
  timestamps?: string[];
  color: string;
  height?: number;
  /** Y-axis unit, rendered inline next to each tick value (default "৳") */
  yLabel?: string;
  /** Optional axis title drawn above the chart */
  title?: string;
  /** Dotted burn-rate projection to depletion */
  showProjection?: boolean;
  burnRatePerMin?: number;
  /** Area-fill opacity under the line */
  fillAlpha?: number;
}

const PAD = { top: 18, right: 16, bottom: 26, left: 52 };

const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW    = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

function pad2(n: number): string { return String(n).padStart(2, "0"); }

/** Choose label format based on span in ms. */
function pickTimeFmt(spanMs: number): (d: Date) => string {
  const H = 3600 * 1000;
  if (spanMs <= 6 * H)      return (d) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  if (spanMs <= 48 * H)     return (d) => `${DOW[d.getDay()]} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  return (d) => `${MONTHS[d.getMonth()]} ${pad2(d.getDate())}`;
}

/** Compact BDT tick: 1,234,567 → "৳1.23M". */
function fmtBDTTick(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000) return `${(a / 1_000_000).toFixed(a >= 10_000_000 ? 0 : 2)}M`;
  if (a >= 1000)      return `${(a / 1000).toFixed(a >= 10_000 ? 0 : 1)}k`;
  return Math.round(n).toString();
}

/** Pick a "nice" y-axis max (1, 2, 2.5, 5 × 10^n). */
function niceCeil(rawMax: number): number {
  if (rawMax <= 0) return 1;
  const exp = Math.floor(Math.log10(rawMax));
  const base = Math.pow(10, exp);
  const m = rawMax / base;
  let nice: number;
  if (m <= 1)      nice = 1;
  else if (m <= 2) nice = 2;
  else if (m <= 2.5) nice = 2.5;
  else if (m <= 5) nice = 5;
  else             nice = 10;
  return nice * base;
}

export function LineChart({
  data,
  timestamps,
  color,
  height = 170,
  yLabel = "৳",
  showProjection = false,
  burnRatePerMin = 0,
  fillAlpha = 0.10,
  title,
}: LineChartProps) {
  const points = (data ?? []).slice();

  if (points.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "#94a3b8", padding: "10px 0" }}>
        not enough data yet
      </div>
    );
  }

  // Compute viewBox width from data length — scales nicely for 5..200 points.
  const totalW = Math.max((points.length || 8) * 14 + 80, 320);
  const plotW = totalW - PAD.left - PAD.right;
  const plotH = height - PAD.top - PAD.bottom;

  // Y-domain
  let maxRaw = Math.max(...points, 1);
  // For projection, extend range down to depletion point.
  let projSteps = 0;
  if (showProjection && burnRatePerMin > 0 && points.length >= 2) {
    const last = points[points.length - 1];
    const minutesLeft = last / burnRatePerMin;
    const capMin = 30 * 24 * 60; // cap projection at 30 days for safety
    const projected = Math.max(0, last - burnRatePerMin * Math.min(minutesLeft, capMin));
    if (projected === 0) maxRaw = Math.max(maxRaw, last * 1.05);
  }
  // Always start y-axis at 0 for currency (cash is never negative).
  const min = 0;
  const max = Math.max(niceCeil(maxRaw), 1);

  // X-domain: derive from timestamps when available, fall back to index.
  const parsedTs: Array<Date | null> = timestamps
    ? timestamps.map((s) => {
        const d = new Date(s);
        return isNaN(d.getTime()) ? null : d;
      })
    : [];

  const xMaxIdx = points.length - 1;
  let xMinMs = 0;
  let xMaxMs = 0;
  let useTimeAxis = false;
  if (parsedTs.length === points.length && parsedTs[0] && parsedTs[xMaxIdx]) {
    xMinMs = parsedTs[0].getTime();
    xMaxMs = parsedTs[xMaxIdx].getTime();
    useTimeAxis = xMaxMs > xMinMs;
  }
  const spanMs = useTimeAxis ? xMaxMs - xMinMs : 0;

  // Projection: expressed as additional steps in the time-domain.
  if (showProjection && burnRatePerMin > 0 && useTimeAxis && spanMs > 0) {
    const projMs = (points[points.length - 1] / burnRatePerMin) * 60_000;
    projSteps = Math.max(0, Math.min(1, projMs / spanMs));
  }

  // X / Y scale helpers (declared before any consumer that uses them).
  const xIdxToMs = (i: number) =>
    useTimeAxis
      ? xMinMs + ((xMaxMs - xMinMs) * i) / Math.max(xMaxIdx, 1)
      : 0;
  const x = (i: number) => {
    if (useTimeAxis && spanMs > 0) {
      const ms = xIdxToMs(i);
      return PAD.left + ((ms - xMinMs) / spanMs) * plotW;
    }
    return PAD.left + (i / Math.max(xMaxIdx, 1)) * plotW;
  };
  const y = (v: number) => PAD.top + plotH - ((v - min) / (max - min)) * plotH;

  // Projection in pixel terms (dotted line from last point straight to zero crossing).
  const projLinePts = (() => {
    if (!showProjection || burnRatePerMin <= 0) return "";
    const last = points[points.length - 1];
    if (last <= 0) return "";
    const pts: string[] = [];
    const xLast = useTimeAxis
      ? PAD.left + plotW // right edge of plot
      : PAD.left + (xMaxIdx / Math.max(xMaxIdx, 1)) * plotW;
    pts.push(`${xLast},${y(last)}`);
    pts.push(`${xLast + Math.max(plotW * projSteps, 30)},${y(0)}`);
    return pts.join(" ");
  })();

  const linePts = points.map((v, i) => `${x(i)},${y(v)}`).join(" ");

  // Area fill (under line, down to y=0)
  const fillPts = (() => {
    if (points.length < 2) return "";
    const baseY = y(0);
    const first = `${x(0)},${baseY}`;
    const lastX = `${x(xMaxIdx)},${baseY}`;
    const middle = points.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    return `${first} ${middle} ${lastX}`;
  })();

  // ---- Y ticks (5 lines, nice values) ----
  const yTickCount = 4;
  const yTicks = Array.from({ length: yTickCount + 1 }, (_, i) => {
    const v = (max * i) / yTickCount;
    return { v, y: y(v), label: fmtBDTTick(v) };
  });

  // ---- X ticks: spread across the plot with timestamp labels ----
  const X_TICK_COUNT = 5;
  const xTicks: Array<{ x: number; label: string; sub?: string }> = [];
  if (points.length >= 2) {
    const fmt = useTimeAxis ? pickTimeFmt(spanMs) : null;
    for (let i = 0; i < X_TICK_COUNT; i += 1) {
      const t = i / (X_TICK_COUNT - 1);
      const idxF = t * xMaxIdx;
      const idx = Math.round(idxF);
      const px = useTimeAxis
        ? PAD.left + t * plotW
        : PAD.left + (idx / Math.max(xMaxIdx, 1)) * plotW;
      let label = "";
      if (useTimeAxis && fmt) {
        // Use actual timestamp at this index (clamped).
        const d = parsedTs[Math.min(idx, parsedTs.length - 1)] || parsedTs[0];
        if (d) label = fmt(d);
      } else {
        label = i === 0 ? "start" : i === X_TICK_COUNT - 1 ? "now" : "";
      }
      xTicks.push({ x: px, label });
    }
  }

  const lastPt = points[points.length - 1];

  return (
    <div style={{ width: "100%" }}>
      {title && (
        <div
          style={{
            fontSize: 11,
            color: "#64748b",
            textTransform: "uppercase",
            letterSpacing: 1,
            marginBottom: 6,
            fontWeight: 600,
          }}
        >
          {title}
        </div>
      )}
      <svg
        width="100%"
        height={height}
        viewBox={`0 0 ${totalW} ${height}`}
        preserveAspectRatio="none"
        style={{ display: "block", overflow: "visible" }}
      >
        {/* Y gridlines + amount labels */}
        {yTicks.map((t, i) => (
          <g key={`yt-${i}`}>
            <line
              x1={PAD.left}
              y1={t.y}
              x2={totalW - PAD.right}
              y2={t.y}
              stroke="#e2e8f0"
              strokeWidth="1"
            />
            <text
              x={PAD.left - 8}
              y={t.y + 4}
              fontSize="12"
              fill="#475569"
              textAnchor="end"
              fontWeight={i === yTicks.length - 1 ? 600 : 400}
            >
              {yLabel}{t.label}
            </text>
          </g>
        ))}

        {/* Axis lines */}
        <line x1={PAD.left} y1={PAD.top} x2={PAD.left} y2={PAD.top + plotH} stroke="#cbd5e1" strokeWidth="1" />
        <line x1={PAD.left} y1={PAD.top + plotH} x2={totalW - PAD.right} y2={PAD.top + plotH} stroke="#cbd5e1" strokeWidth="1" />

        {/* X tick marks + labels */}
        {xTicks.map((t, i) => (
          <g key={`xt-${i}`}>
            <line
              x1={t.x}
              y1={PAD.top + plotH}
              x2={t.x}
              y2={PAD.top + plotH + 3}
              stroke="#cbd5e1"
              strokeWidth="1"
            />
            <text
              x={t.x}
              y={PAD.top + plotH + 16}
              fontSize="12"
              fill="#475569"
              textAnchor={
                i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"
              }
              fontWeight={i === 0 || i === xTicks.length - 1 ? 600 : 400}
            >
              {t.label}
            </text>
          </g>
        ))}

        {/* Area fill under the line */}
        {fillPts && (
          <polygon points={fillPts} fill={color} opacity={fillAlpha} />
        )}

        {/* Projection dotted line */}
        {projLinePts && (
          <polyline
            points={projLinePts}
            fill="none"
            stroke={color}
            strokeWidth="1.5"
            strokeDasharray="4 4"
            opacity="0.6"
          />
        )}

        {/* Main line */}
        <polyline points={linePts} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />

        {/* Last-point dot */}
        <circle cx={x(xMaxIdx)} cy={y(lastPt)} r="5" fill={color} opacity="0.22" />
        <circle cx={x(xMaxIdx)} cy={y(lastPt)} r="3" fill={color} />

        {/* Depletion marker at end of projection */}
        {projLinePts && (() => {
          const segs = projLinePts.split(" ").map((p) => p.split(",").map(Number));
          const lastSeg = segs[segs.length - 1];
          if (!lastSeg) return null;
          const [px, py] = lastSeg;
          return (
            <g>
              <line x1={px} y1={PAD.top} x2={px} y2={py} stroke={color} strokeDasharray="2 3" opacity="0.45" />
              <circle cx={px} cy={py} r="3.5" fill="#fff" stroke={color} strokeWidth="1.6" />
              <text x={px - 6} y={py - 6} fontSize="10" fill={color} textAnchor="end" fontWeight="700">
                ৳0
              </text>
            </g>
          );
        })()}
      </svg>

      {/* Legend / span caption */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: 12,
          color: "#94a3b8",
          marginTop: 6,
          letterSpacing: 0.3,
        }}
      >
        <span>
          {points.length} pts
          {useTimeAxis && spanMs > 0
            ? ` · ${humanSpan(spanMs)}`
            : ""}
        </span>
        {showProjection && burnRatePerMin > 0 && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span
              style={{
                display: "inline-block",
                width: 14,
                height: 0,
                borderTop: `2px dashed ${color}`,
                opacity: 0.7,
              }}
            />
            projected
          </span>
        )}
      </div>
    </div>
  );
}

/** Render a human span like "2h 14m", "1d 6h", "3w". */
function humanSpan(ms: number): string {
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) {
    const rem = m % 60;
    return rem ? `${h}h ${rem}m` : `${h}h`;
  }
  const d = Math.floor(h / 24);
  if (d < 14) {
    const remH = h % 24;
    return remH ? `${d}d ${remH}h` : `${d}d`;
  }
  const w = Math.floor(d / 7);
  const remD = d % 7;
  return remD ? `${w}w ${remD}d` : `${w}w`;
}
