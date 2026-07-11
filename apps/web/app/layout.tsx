"use client";
import React, { useEffect, useState } from "react";
import { TopBar } from "../components/TopBar";
import { PrincipalProvider, usePrincipal } from "../components/PrincipalProvider";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{
        margin: 0,
        background: "#f1f5f9",
        color: "#0f172a",
        fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
        // Slightly larger root size so the entire UI reads comfortably
        // at 100% zoom instead of looking like a 90% shrink-to-fit page.
        fontSize: 15,
      }}>
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
      {/*
        Edge-to-edge layout: the main container fills the viewport
        horizontally and only uses symmetric side padding as its gutter.
        No max-width clamp, so on wide monitors the content (and cards)
        spread out instead of leaving empty side bands of body background.
      */}
      <main
        style={{
          width: "100%",
          margin: 0,
          padding: "28px 32px",
          boxSizing: "border-box",
        }}
      >
        {children}
      </main>
    </>
  );
}