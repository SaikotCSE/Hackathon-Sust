"use client";
import React, { useEffect, useState } from "react";
import { TopBar } from "../components/TopBar";
import { PrincipalProvider, usePrincipal } from "../components/PrincipalProvider";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0, background: "#f1f5f9", color: "#0f172a", fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif" }}>
        <PrincipalProvider>
          <Shell>{children}</Shell>
        </PrincipalProvider>
      </body>
    </html>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { principal, setPrincipal } = usePrincipal();
  return (
    <>
      <TopBar principal={principal} onPrincipalChange={setPrincipal} />
      <main style={{ maxWidth: 1180, margin: "0 auto", padding: "24px" }}>{children}</main>
    </>
  );
}