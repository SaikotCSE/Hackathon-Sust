"use client";
import React from "react";
import useSWR from "swr";
import type { DashboardProvider, DashboardSeriesResponse } from "../lib/types";
import { client } from "../lib/client";
import { ProviderLiquidityCard } from "./ProviderLiquidityCard";
import { LineChart } from "./LineChart";

const PROVIDER_DOT: Record<string, string> = {
  physical: "#1e293b",
  bkash:    "#e11d48",
  nagad:    "#ea580c",
  rocket:   "#7c3aed",
};

/**
 * Wraps ProviderLiquidityCard with a real time-series line chart that has
 * x/y axes, gridlines, real time labels, and an optional burn-rate
 * projection to depletion. The chart is rendered INSIDE the card body,
 * directly below the big balance amount, so amount → trend → projection
 * reads as one continuous block.
 *
 * Data fetching lives here (separate SWR key per provider) so each card
 * refreshes independently at a coarser interval than the dashboard summary.
 */
export function ProviderChartCard({
  p,
  agentId,
}: {
  p: DashboardProvider;
  agentId: number;
}) {
  const { data } = useSWR<DashboardSeriesResponse>(
    ["dashboard-series", agentId, p.provider],
    () => client.getDashboardSeries(agentId, p.provider),
    { refreshInterval: 3000 }
  );
  const series = data?.series?.[0];
  const color = PROVIDER_DOT[p.provider] ?? "#64748b";
  const points = (series?.points ?? []).map(pt => pt.balance);
  const timestamps = (series?.points ?? []).map(pt => pt.ts);
  const burn = series?.burn_rate_per_min ?? p.burn_rate_per_min ?? 0;
  const projected = burn > 0 && points.length > 0 && (points[points.length - 1] / burn) < 30 * 60;

  const chartSlot = (
    <LineChart
      data={points}
      timestamps={timestamps}
      color={color}
      height={140}
      showProjection={projected}
      burnRatePerMin={burn}
    />
  );

  return <ProviderLiquidityCard p={p} chart={chartSlot} />;
}
