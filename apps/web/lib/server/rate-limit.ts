/**
 * Small fixed-window limiter for the auth endpoints. IN-PROCESS and per instance (Railway runs one web instance that sleeps when idle), so it is a
 * brute-force speed bump, not a distributed guarantee: Neon Auth applies its own limits upstream and the API rate-limits every call after sign-in.
 * Keys are hashed by the caller; the map is bounded and expired windows are swept so memory cannot grow without limit.
 */
const MAX_KEYS = 5000;
const windows = new Map<string, { count: number; resetAt: number }>();

export type Limit = { allowed: boolean; retryAfterS: number };

export function hit(key: string, max: number, windowMs: number, now = Date.now()): Limit {
  if (windows.size >= MAX_KEYS) {
    for (const [k, w] of windows) if (w.resetAt <= now) windows.delete(k);
    if (windows.size >= MAX_KEYS) windows.delete(windows.keys().next().value as string);
  }
  const w = windows.get(key);
  if (!w || w.resetAt <= now) {
    windows.set(key, { count: 1, resetAt: now + windowMs });
    return { allowed: true, retryAfterS: 0 };
  }
  w.count += 1;
  return w.count <= max ? { allowed: true, retryAfterS: 0 } : { allowed: false, retryAfterS: Math.max(1, Math.ceil((w.resetAt - now) / 1000)) };
}

/** Clear a window (e.g. after a successful sign-in) so legitimate users are not penalised for earlier typos. */
export const reset = (key: string) => void windows.delete(key);
export const clearForTests = () => windows.clear();

/** Client address for limiter keys. Behind Railway's proxy the LAST X-Forwarded-For entry is the one the proxy appended; earlier ones are client-supplied. */
export function clientIp(h: { get(name: string): string | null }): string {
  const xff = (h.get("x-forwarded-for") ?? "").split(",").map((x) => x.trim()).filter(Boolean);
  return (xff.at(-1) ?? h.get("x-real-ip") ?? "unknown").slice(0, 64);
}
