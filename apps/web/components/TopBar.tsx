"use client";
import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import useSWR from "swr";
import { client } from "../lib/client";
import { can, tierFor } from "../lib/rbac";
import type { Principal as PrincipalT } from "../components/PrincipalProvider";

interface NavItem { href: string; label: string; show: (role: string) => boolean }

const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", show: () => true },
  { href: "/alerts",    label: "Alerts",    show: () => true },
  { href: "/cases",     label: "Cases",     show: (r) => can(r, "can_view_cases") },
  { href: "/metrics",   label: "Metrics",   show: (r) => can(r, "can_view_metrics") },
];

const ROLE_BADGE: Record<string, { bg: string; fg: string }> = {
  agent:      { bg: "#1d4ed8", fg: "#fff" },
  ops:        { bg: "#0ea5e9", fg: "#fff" },
  risk:       { bg: "#7c3aed", fg: "#fff" },
  provider:   { bg: "#9333ea", fg: "#fff" },
  management: { bg: "#475569", fg: "#fff" },
};

export function TopBar({
  principal,
  onPrincipalChange,
}: {
  principal?: PrincipalT;
  onPrincipalChange: (p: PrincipalT | undefined) => void;
}) {
  const path = usePathname();
  const { data } = useSWR("users", () => client.getUsers(), { revalidateOnFocus: false });
  const role = principal?.role ?? "agent";
  const badge = ROLE_BADGE[role] ?? ROLE_BADGE.agent;

  return (
    <header style={{
      background: "#0f172a", color: "#f8fafc", padding: "10px 24px",
      display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap",
    }}>
      <div style={{ fontWeight: 700, fontSize: 16 }}>Super Agent · Liquidity & Risk</div>
      <nav style={{ display: "flex", gap: 4, marginLeft: 8 }}>
        {NAV.filter(n => n.show(role)).map(n => {
          const active = path?.startsWith(n.href);
          return (
            <Link key={n.href} href={n.href} style={{
              padding: "6px 12px", borderRadius: 6, fontSize: 13,
              background: active ? "#1e293b" : "transparent",
              color: active ? "#fff" : "#cbd5e1",
              textDecoration: "none",
            }}>{n.label}</Link>
          );
        })}
      </nav>
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 11, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 1 }}>
          Role
        </span>
        <span style={{
          background: badge.bg, color: badge.fg, fontSize: 11, fontWeight: 700,
          padding: "3px 8px", borderRadius: 999, textTransform: "uppercase",
        }}>{role}</span>
        <select
          aria-label="Acting as principal"
          value={principal?.username ?? "agent"}
          onChange={(e) => {
            const u = data?.users.find(x => x.username === e.target.value);
            if (u) onPrincipalChange({
              username: u.username,
              display_name: u.display_name,
              role: u.role,
              provider: u.provider,
              area: u.area,
            });
          }}
          style={{
            background: "#1e293b", color: "#f8fafc", border: "1px solid #334155",
            borderRadius: 6, padding: "4px 8px", fontSize: 12, minWidth: 220,
          }}
        >
          {(data?.users ?? []).map(u => (
            <option key={u.username} value={u.username}>
              {u.display_name} · {tierFor(u.username, u.role)}{u.provider ? ` · ${u.provider}` : ""}
            </option>
          ))}
        </select>
      </div>
    </header>
  );
}