import React from "react";
import Link from "next/link";

export default function Home() {
  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700 }}>Super Agent Liquidity & Risk Intelligence Platform</h1>
      <p style={{ color: "#475569" }}>
        Decision-support prototype. Human-in-the-loop only — never executes transactions,
        never claims fraud.
      </p>
      <ul>
        <li><Link href="/dashboard">Dashboard</Link> — provider balances, forecasts, top recommendation</li>
        <li><Link href="/alerts">Alerts</Link> — explainable, prioritized, auditable</li>
        <li><Link href="/cases">Cases</Link> — workflow state machine (open → assigned → reviewing → resolved)</li>
        <li><Link href="/metrics">Metrics</Link> — false-positive rate, calibration, MTTR</li>
      </ul>
    </div>
  );
}