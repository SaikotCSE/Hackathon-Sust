"use client";
import React, { createContext, useContext, useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { client } from "../lib/client";

export interface Principal {
  username: string;
  display_name: string;
  role: string;
  provider?: string | null;
  area?: string | null;
}

interface Ctx {
  principal?: Principal;
  /** True once the client has finished its first hydration pass. While
   *  false, callers MUST render exactly what the server would render — i.e.
   *  treat the principal as unknown. We expose this so dashboards can
   *  suppress fetches that would otherwise fire with the wrong identity. */
  mounted: boolean;
  setPrincipal: (p: Principal | undefined) => void;
}

const PrincipalCtx = createContext<Ctx>({ principal: undefined, mounted: false, setPrincipal: () => {} });

const STORAGE_KEY = "sa_principal_v2";

const FALLBACK: Principal = {
  username: "agent",
  display_name: "Multi-Provider Agent",
  role: "agent",
  provider: null,
  area: "Dhaka",
};

// Read the stored principal on the client. ONLY call this from an effect —
// calling it during render would diverge from the server render and throw
// a Next.js hydration mismatch (server has no localStorage, client does).
function readStoredPrincipal(): Principal | undefined {
  if (typeof window === "undefined") return undefined;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Principal;
      if (parsed?.username) return parsed;
    }
  } catch {}
  return undefined;
}

export function PrincipalProvider({ children }: { children: React.ReactNode }) {
  // CRITICAL: initial state must be identical on the server and on the
  // *first* client render. The server has no localStorage and no
  // principal, so we start with `undefined` here too. Reading
  // localStorage in the useState initializer (as an earlier version did)
  // makes the client render emit different text — e.g. <span>ops</span> —
  // than the server's <span>agent</span>, and React throws the
  // hydration-mismatch error you see in the console.
  const [principal, setPrincipalState] = useState<Principal | undefined>(undefined);
  // `mounted` flips to true only after the first client effect runs. Use
  // it as the synchronization point: the server and the first client
  // render share `mounted=false` (unknown principal); the second pass,
  // running only on the client, sees `mounted=true` with the real value.
  // This eliminates the SSR/CSR mismatch without a visible flash for the
  // user (the swap is in the same paint as the rest of the layout effect).
  const [mounted, setMounted] = useState(false);

  // Pull the canonical roster from the backend so the picker is always in sync.
  const { data } = useSWR("users", () => client.getUsers(), { revalidateOnFocus: false });

  // Hydrate on mount: read localStorage exactly once, then flip `mounted`.
  // We do this in an effect (not in useState's initializer) so the server
  // render and the first client render produce identical DOM.
  useEffect(() => {
    const stored = readStoredPrincipal();
    setPrincipalState(stored ?? FALLBACK);
    setMounted(true);
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

  const value = useMemo(
    () => ({ principal, mounted, setPrincipal }),
    [principal, mounted]
  );

  // Keep the X-User header in sync so SWR fetches use the right principal.
  // Skip the very first render: `principal` is undefined then and writing
  // the bare default ("agent") here would race with the mount hydration
  // effect above and could clobber the real value for the next reload.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!mounted) return;
    window.localStorage.setItem("sa_user", principal?.username ?? "agent");
  }, [principal, mounted]);

  return <PrincipalCtx.Provider value={value}>{children}</PrincipalCtx.Provider>;
}

export function usePrincipal(): Ctx {
  return useContext(PrincipalCtx);
}
