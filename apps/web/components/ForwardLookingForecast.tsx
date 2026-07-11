"use client";
import React from "react";
import type { DashboardProvider } from "../lib/types";
import { Card } from "./Primitives";

function fmtHours(hours: number | null): string {
  if (hours == null) return "—";
  if (Math.abs(hours) >= 10) return `~${hours.toFixed(1)} hours`;
  if (hours < 1) return `~${Math.round(hours * 60)} min`;
  return `~${hours.toFixed(1)} hours`;
}

export function ForwardLookingForecast({
  providers,
  physical,
}: {
  providers: DashboardProvider[];
  physical?: DashboardProvider | null;
}) {
  const rows = [
    ...providers.filter(p => p.provider !== "physical").map(p => ({
      key: p.provider,
      title: `${p.provider.charAt(0).toUpperCase()}${p.provider.slice(1)} balance may run out in ${fmtHours(p.hours_to_shortage)}.`,
      confidence: p.forecast_confidence,
      basis: `based on the last ${Math.max(1, (p.history ?? []).length)} intervals' average outflow rate`,
      out: true,
    })),
    ...(physical ? [{
      key: "physical",
      title: `Shared cash reserve may run out in ${fmtHours(physical.hours_to_shortage)}.`,
      confidence: physical.forecast_confidence,
      basis: `based on shared cash drawer drawdown over ~${Math.max(1, (physical.history ?? []).length / 2)} hours`,
      out: true,
    }] : []),
  ];
  const sorted = rows.sort((a, b) => (a.confidence - b.confidence));
  return (
    <Card style={{ marginTop: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontWeight: 700, fontSize: 15 }}>Forward-Looking Shortage Forecast</div>
        <div style={{ fontSize: 11, color: "#94a3b8" }}>Time estimate + confidence + data quality</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
        {sorted.map(r => (
          <div key={r.key} style={{
            background: "#f8fafc", border: "1px solid #e5e7eb", borderRadius: 8,
            padding: "10px 12px",
          }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#0f172a" }}>{r.title}</div>
            <div style={{ fontSize: 11, color: "#64748b", marginTop: 4 }}>
              {Math.round((r.confidence ?? 0) * 100)}% confidence · data ok
            </div>
            <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>{r.basis}</div>
          </div>
        ))}
        {rows.length === 0 && (
          <div style={{ color: "#94a3b8", fontSize: 13 }}>All projections nominal.</div>
        )}
      </div>
    </Card>
  );
}