"use client";
import React, { useState } from "react";
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
  const { data: inbox, mutate: refreshInbox } = useSWR(
    principal ? ["notifications", principal.username] : null,
    () => client.getNotifications(),
    { refreshInterval: 3000 },
  );
  const [showInbox, setShowInbox] = useState(false);
  const role = principal?.role ?? "agent";
  const badge = ROLE_BADGE[role] ?? ROLE_BADGE.agent;

  return (
    <header style={{
      background: "#0f172a", color: "#f8fafc",
      // Edge-to-edge bar: spans full viewport width. The interior padding
      // matches the <main> gutter so the logo and nav stay visually
      // aligned with the content below.
      width: "100%",
      padding: "14px 32px",
      boxSizing: "border-box",
      display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap",
    }}>
      <div style={{ fontWeight: 700, fontSize: 18 }}>Super Agent · Liquidity & Risk</div>
      <nav style={{ display: "flex", gap: 4, marginLeft: 12 }}>
        {NAV.filter(n => n.show(role)).map(n => {
          const active = path?.startsWith(n.href);
          return (
            <Link key={n.href} href={n.href} style={{
              padding: "8px 14px", borderRadius: 6, fontSize: 15,
              background: active ? "#1e293b" : "transparent",
              color: active ? "#fff" : "#cbd5e1",
              textDecoration: "none",
            }}>{n.label}</Link>
          );
        })}
      </nav>
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ position: "relative" }}>
          <button
            onClick={() => setShowInbox(v => !v)}
            aria-label="Stakeholder notifications"
            style={{ background: "#1e293b", color: "#fff", border: "1px solid #334155", borderRadius: 8, padding: "6px 10px", cursor: "pointer" }}
          >
            🔔 {inbox?.unread ?? 0}
          </button>
          {showInbox && (
            <div style={{ position: "absolute", right: 0, top: 40, width: 390, maxHeight: 430, overflowY: "auto", background: "#fff", color: "#0f172a", border: "1px solid #cbd5e1", borderRadius: 10, boxShadow: "0 12px 30px rgba(15,23,42,.22)", zIndex: 1000 }}>
              <div style={{ padding: 12, fontWeight: 700, borderBottom: "1px solid #e5e7eb" }}>Stakeholder inbox</div>
              {(inbox?.notifications ?? []).map(n => (
                <Link
                  key={n.id}
                  href={`/alerts/${n.alert_id}`}
                  onClick={async () => { await client.markNotificationRead(n.id); refreshInbox(); setShowInbox(false); }}
                  style={{ display: "block", padding: 12, borderBottom: "1px solid #f1f5f9", background: n.read_at ? "#fff" : "#eff6ff", color: "inherit", textDecoration: "none" }}
                >
                  <div style={{ fontWeight: n.read_at ? 600 : 800, fontSize: 13 }}>{n.title}</div>
                  <div style={{ fontSize: 12, color: "#475569", marginTop: 3 }}>{n.message}</div>
                  <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 4 }}>{new Date(n.created_at).toLocaleString()} · {n.actor}</div>
                </Link>
              ))}
              {(inbox?.notifications.length ?? 0) === 0 && <div style={{ padding: 14, color: "#64748b", fontSize: 13 }}>No case handoffs yet.</div>}
            </div>
          )}
        </div>
        <span style={{ fontSize: 13, color: "#94a3b8", textTransform: "uppercase", letterSpacing: 1 }}>
          Role
        </span>
        <span style={{
          background: badge.bg, color: badge.fg, fontSize: 13, fontWeight: 700,
          padding: "4px 10px", borderRadius: 999, textTransform: "uppercase",
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
            borderRadius: 6, padding: "6px 10px", fontSize: 14, minWidth: 240,
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
