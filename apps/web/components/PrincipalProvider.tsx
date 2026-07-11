"use client";
import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { client } from "../lib/client";
import type { UserInfo } from "../lib/types";

export interface Principal {
  username: string;
  display_name: string;
  role: string;
  provider?: string | null;
  area?: string | null;
}

interface Ctx {
  principal?: Principal;
  setPrincipal: (p: Principal | undefined) => void;
}

const PrincipalCtx = createContext<Ctx>({ principal: undefined, setPrincipal: () => {} });

const STORAGE_KEY = "sa_principal_v2";

const FALLBACK: Principal = {
  username: "agent",
  display_name: "Multi-Provider Agent",
  role: "agent",
  provider: null,
  area: "Dhaka",
};

export function PrincipalProvider({ children }: { children: React.ReactNode }) {
  const [principal, setPrincipalState] = useState<Principal | undefined>(undefined);
  // Pull the canonical roster from the backend so the picker is always in sync.
  const { data } = useSWR("users", () => client.getUsers(), { revalidateOnFocus: false });

  // Hydrate from localStorage after mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      try {
        const parsed = JSON.parse(raw) as Principal;
        if (parsed?.username) setPrincipalState(parsed);
      } catch {}
    } else {
      setPrincipalState(FALLBACK);
    }
  }, []);

  // If the stored username isn't in the roster any more, snap back to fallback.
  useEffect(() => {
    if (!data?.users || !principal) return;
    const found = data.users.find(u => u.username === principal.username);
    if (!found) {
      setPrincipalState(FALLBACK);
    }
  }, [data, principal]);

  const setPrincipal = (p: Principal | undefined) => {
    setPrincipalState(p);
    if (typeof window !== "undefined") {
      if (p) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
      else window.localStorage.removeItem(STORAGE_KEY);
    }
  };

  const value = useMemo(() => ({ principal, setPrincipal }), [principal]);

  // Keep the X-User header in sync so SWR fetches use the right principal.
  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem("sa_user", principal?.username ?? "agent");
  }, [principal]);

  return <PrincipalCtx.Provider value={value}>{children}</PrincipalCtx.Provider>;
}

export function usePrincipal(): Ctx {
  return useContext(PrincipalCtx);
}