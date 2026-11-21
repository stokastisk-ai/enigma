import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { apiBase, cognitoSelected } from "@/lib/server/env";
import { sameOrigin } from "@/lib/server/cognito";
import { getSession } from "@/lib/server/session";
import { ConversationCreate, ProjectCreate, RuntimeCreate, SubmitJob, WorkspaceCreate } from "@/lib/contracts";

/**
 * Authenticated BFF. The browser never talks to FastAPI directly and never sees a bearer token:
 * this route verifies the session (Cognito, Clerk or the guarded dev login), then forwards ONLY allow-listed method+path combinations with a validated body.
 * Anything not listed is a 404, so the proxy cannot be used to reach other upstream paths (no SSRF / path traversal).
 */
const ID = "[0-9a-fA-F-]{36}";
type Rule = { method: "GET" | "POST"; re: RegExp; body?: z.ZodType; idempotent?: boolean };
const R = (method: Rule["method"], pattern: string, extra: Partial<Rule> = {}): Rule => ({ method, re: new RegExp(`^${pattern}$`), ...extra });

const RULES: Rule[] = [
  R("GET", "me"),
  R("GET", "workspaces"),
  R("POST", "workspaces", { body: WorkspaceCreate }),
  R("GET", `workspaces/${ID}/projects`),
  R("POST", `workspaces/${ID}/projects`, { body: ProjectCreate }),
  R("GET", `projects/${ID}/(runtimes|conversations|overview|runs)`),
  R("POST", `projects/${ID}/runtimes`, { body: RuntimeCreate }),
  R("POST", `projects/${ID}/conversations`, { body: ConversationCreate }),
  R("GET", `projects/${ID}/metrics/(gpu|vllm)`),
  R("GET", `conversations/${ID}/messages`),
  R("POST", `conversations/${ID}/jobs`, { body: SubmitJob, idempotent: true }),
  R("GET", `jobs/${ID}`),
  R("POST", `jobs/${ID}/cancel`),
  R("GET", `runs/${ID}`),
  R("POST", `runtimes/${ID}/(rotate|revoke)`),
  R("GET", `runtimes/${ID}/credentials`),
];
const QUERY_KEYS = new Set(["after_seq", "range", "runtime_id", "max_points", "expires_days"]);
const MAX_BODY = 128 * 1024;
const err = (status: number, code: string, message: string) => NextResponse.json({ error: { code, message } }, { status, headers: { "cache-control": "no-store" } });

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  const method = req.method as Rule["method"];
  const rule = RULES.find((r) => r.method === method && r.re.test(joined));
  if (!rule) return err(404, "not_found", "not found");

  // Cognito sessions are ambient httpOnly cookies, so state-changing requests must be same-origin (SameSite=Lax is the second layer).
  if (method === "POST" && cognitoSelected() && !sameOrigin(req.headers.get("origin"), req.headers.get("host"), req.headers.get("x-forwarded-host"), process.env.ENIGMA_PUBLIC_HOST))
    return err(403, "bad_origin", "cross-origin request refused");
  const session = await getSession();
  if (!session) return err(401, "unauthorized", "authentication required");
  const base = apiBase();
  if (!base) return err(503, "api_not_configured", "ENIGMA_API_URL is not set");

  const qs = new URLSearchParams();
  for (const [k, v] of req.nextUrl.searchParams) {
    if (!QUERY_KEYS.has(k) || !/^[\w.-]{1,40}$/.test(v)) return err(422, "invalid_query", "invalid query parameter");
    qs.set(k, v);
  }

  const headers: Record<string, string> = { authorization: `Bearer ${session.token}`, accept: "application/json" };
  const rid = req.headers.get("x-request-id");
  if (rid && /^[A-Za-z0-9._-]{8,64}$/.test(rid)) headers["x-request-id"] = rid;
  let body: string | undefined;
  if (method === "POST") {
    if (Number(req.headers.get("content-length") ?? 0) > MAX_BODY) return err(413, "payload_too_large", "body too large");
    const raw = await req.text();
    if (raw.length > MAX_BODY) return err(413, "payload_too_large", "body too large");
    if (rule.body) {
      let json: unknown;
      try {
        json = raw ? JSON.parse(raw) : {};
      } catch {
        return err(422, "invalid_json", "body is not valid JSON");
      }
      const parsed = rule.body.safeParse(json);
      if (!parsed.success) return err(422, "validation_error", "request validation failed"); // never echo the body (it may hold a prompt)
      body = JSON.stringify(parsed.data);
    }
    if (rule.idempotent) {
      const key = req.headers.get("idempotency-key") ?? "";
      if (!/^[A-Za-z0-9._:-]{8,80}$/.test(key)) return err(422, "idempotency_key_required", "Idempotency-Key header is required");
      headers["idempotency-key"] = key;
    }
    if (body !== undefined) headers["content-type"] = "application/json";
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${base}/api/v1/app/${joined}${qs.size ? `?${qs}` : ""}`, { method, headers, body, signal: AbortSignal.timeout(20_000), cache: "no-store" });
  } catch {
    return err(502, "upstream_unreachable", "the Enigma API is unreachable");
  }
  const text = await upstream.text();
  const out = new NextResponse(text, { status: upstream.status, headers: { "content-type": upstream.headers.get("content-type") ?? "application/json", "cache-control": "no-store" } });
  const echoed = upstream.headers.get("x-request-id");
  if (echoed) out.headers.set("x-request-id", echoed);
  const retry = upstream.headers.get("retry-after");
  if (retry) out.headers.set("retry-after", retry);
  return out;
}

export { handle as GET, handle as POST };
