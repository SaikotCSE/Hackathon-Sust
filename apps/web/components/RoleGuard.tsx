"use client";
import React from "react";
import { Role, can } from "../lib/rbac";

export function RoleGuard({
  role,
  capability,
  allow,
  fallback = null,
  children,
}: {
  role?: string;
  capability?: Parameters<typeof can>[1];
  allow?: Role[];
  fallback?: React.ReactNode;
  children: React.ReactNode;
}) {
  const okByCap = capability ? can(role, capability) : true;
  const okByAllow = allow ? (role ? allow.includes(role as Role) : false) : true;
  if (!okByCap || !okByAllow) return <>{fallback}</>;
  return <>{children}</>;
}