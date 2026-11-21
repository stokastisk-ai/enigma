/** Browser API client. Talks only to the same-origin BFF (/api/enigma/*); the BFF holds the credentials. */
export class ApiFailure extends Error {
  constructor(public status: number, public code: string, message: string, public correlationId?: string, public extra: Record<string, unknown> = {}) {
    super(message);
  }
  get isOfflineish() { return this.status === 0 || this.status === 502 || this.status === 503 || this.status === 504; }
}

export async function api<T>(path: string, opts: { method?: "GET" | "POST"; body?: unknown; headers?: Record<string, string>; signal?: AbortSignal } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/enigma/${path}`, {
      method: opts.method ?? "GET",
      headers: { ...(opts.body !== undefined ? { "content-type": "application/json" } : {}), ...opts.headers },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiFailure(0, "network_error", "Cannot reach the server. Check your connection.");
  }
  const text = await res.text();
  let json: unknown = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* non-JSON error page */ }
  if (!res.ok) {
    const e = (json as { error?: { code?: string; message?: string; correlation_id?: string } & Record<string, unknown> } | null)?.error;
    const { code, message, correlation_id, ...extra } = e ?? {};
    throw new ApiFailure(res.status, code ?? "http_error", message ?? `Request failed (${res.status})`, correlation_id ?? res.headers.get("x-request-id") ?? undefined, extra);
  }
  return json as T;
}

export const newKey = () => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `k-${Date.now()}-${Math.random().toString(36).slice(2)}`);
