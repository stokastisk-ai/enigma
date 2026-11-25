import { redirect } from "next/navigation";
import { requireSessionOr } from "./page-guard-core";
import { getSession, type Session } from "./session";

/**
 * Resource-level authorization for server layouts/pages (defence in depth behind `proxy.ts`).
 * Works for Neon sessions and the guarded dev auth. With no provider configured and dev auth not allowed, `getSession()` is null and the
 * visitor is sent to /sign-in, so an unconfigured deployment fails closed here too.
 */
export const requirePageSession = (): Promise<Session> => requireSessionOr(getSession, () => redirect("/sign-in"));
