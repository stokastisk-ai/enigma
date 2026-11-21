"use client";
import { createContext, useContext } from "react";
import { useAuth } from "@clerk/nextjs";

export type RealtimeConfig = { url: string; anonKey: string; getToken: () => Promise<string | null> };
const Ctx = createContext<RealtimeConfig | null>(null);
export const useRealtimeConfig = () => useContext(Ctx);

/** Supplies the browser's Clerk session token to Supabase Realtime (third-party auth). Only mounted when Clerk + Supabase public env exist. */
export function RealtimeBridge({ url, anonKey, children }: { url: string; anonKey: string; children: React.ReactNode }) {
  const { getToken } = useAuth();
  return <Ctx.Provider value={{ url, anonKey, getToken: () => getToken() }}>{children}</Ctx.Provider>;
}
