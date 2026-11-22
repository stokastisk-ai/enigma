/** Session-aware polling. Kaggle sessions are 1-2 h; while no runtime is live the console must not keep hammering the API (each request is billed). */
export type SessionStatus = "online" | "stale" | "offline" | "never_connected" | undefined | null;

export const RUNTIME_POLL_LIVE_MS = 5_000;
export const RUNTIME_POLL_IDLE_MS = 30_000;   // only used to notice that a session has (re)started

export const isLive = (s: SessionStatus) => s === "online" || s === "stale";

/** Interval for metric/overview polling: the caller's cadence while live, 0 (load once, keep history, no timer) while offline. */
export function metricsPollMs(status: SessionStatus, liveMs: number): number {
  return isLive(status) ? liveMs : 0;
}

/** Interval for the runtime-list poll itself: fast while any runtime is live, slow otherwise (so a new session is noticed). */
export function runtimeListPollMs(statuses: readonly SessionStatus[]): number {
  return statuses.some(isLive) ? RUNTIME_POLL_LIVE_MS : RUNTIME_POLL_IDLE_MS;
}
