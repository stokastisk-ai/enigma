import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { sameOrigin } from "@/lib/server/origin";
import * as classify from "@/lib/server/auth-errors";
import { cacheClearAll, getAuth, authEnabled, requireVerified, toSdkError, type SdkError } from "@/lib/server/auth-server";
import { mailConfigured } from "@/lib/server/mailer";
import { passwordProblem } from "@/lib/server/password-policy";
import { clientIp, hit, reset } from "@/lib/server/rate-limit";

/**
 * Self-hosted Better Auth endpoints (sign-in, sign-up, verify, resend, sign-out, me). 404 unless ENIGMA_AUTH=on and configured. Same-origin POSTs only
 * (CSRF; the session cookies are SameSite=Lax httpOnly as a second layer), strict bodies, generic errors (no account enumeration), rate limits per
 * client address and per account, and no token ever reaches the browser. Failures are logged by action + status + error code ONLY (never an e-mail
 * address, password, code or cookie).
 */
const Email = z.string().trim().toLowerCase().email().max(254);
const BODIES = {
  "sign-in": z.object({ email: Email, password: z.string().min(1).max(128) }).strict(),
  "sign-up": z.object({ email: Email, password: z.string().max(128), name: z.string().trim().min(1).max(64).optional() }).strict(),
  verify: z.object({ email: Email, code: z.string().regex(/^\d{6}$/) }).strict(),
  resend: z.object({ email: Email }).strict(),
  "sign-out": z.object({}).strict(),
} as const;
type Action = keyof typeof BODIES;

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  NextResponse.json(body, { status, headers: { "cache-control": "no-store", ...headers } });
const fail = (status: number, code: string, message: string, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}) =>
  json(status, { error: { code, message }, ...extra }, headers);
const h = (s: string) => createHash("sha256").update(s).digest("hex").slice(0, 32);

/** Operational log line: action, HTTP status and upstream error code only. */
function logUpstream(action: string, e: SdkError | undefined) {
  console.warn(`[enigma.auth] action=${action} upstream_status=${e?.status ?? "none"} upstream_code=${String(e?.code ?? "none").replace(/[^A-Za-z0-9_]/g, "").slice(0, 48)}`);
}
function out(action: string, o: classify.Outcome, error?: SdkError | null) {
  if (o.upstream) logUpstream(action, error ?? undefined);
  const headers: Record<string, string> = o.status === 429 ? { "retry-after": "60" } : {};
  return fail(o.status, o.code, o.message, o.needsVerify ? { needsVerify: true } : {}, headers);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (!authEnabled() || !(action in BODIES)) return fail(404, "not_found", "not found");
  if (!sameOrigin(req.headers.get("origin"), req.headers.get("host"), process.env.ENIGMA_PUBLIC_HOST)) return fail(403, "bad_origin", "cross-origin request refused");
  if (Number(req.headers.get("content-length") ?? 0) > 4096) return fail(413, "payload_too_large", "body too large");

  const ip = hit(`auth:ip:${h(clientIp(req.headers))}`, 30, 5 * 60_000);
  if (!ip.allowed) return fail(429, "rate_limited", "Too many attempts. Try again later.", {}, { "retry-after": String(ip.retryAfterS) });

  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return fail(422, "invalid_json", "body is not valid JSON");
  }
  const parsed = BODIES[action as Action].safeParse(raw);
  if (!parsed.success) {
    const emailBad = parsed.error.issues.some((i) => i.path[0] === "email");
    return fail(422, "validation_error", emailBad ? "Enter a valid email address." : action === "verify" ? "Enter the 6-digit code from the email." : "Check the details and try again.");
  }
  const b = parsed.data as { email: string; password: string; code: string; name?: string };

  const auth = await getAuth();
  if (!auth) return fail(503, "auth_unavailable", "authentication is not available");
  const headers = req.headers;
  // Runs a Better Auth server call and returns its result plus the Set-Cookie headers it produced (copied onto our own response).
  const call = async <T,>(fn: () => Promise<{ headers: Headers; response: T }>) => {
    try {
      const r = await fn();
      return { data: r.response, error: null as SdkError, cookies: r.headers.getSetCookie?.() ?? [] };
    } catch (e) {
      return { data: null, error: toSdkError(e), cookies: [] as string[] };
    }
  };
  const withCookies = (res: NextResponse, cookies: string[]) => {
    for (const c of cookies) res.headers.append("set-cookie", c);
    return res;
  };

  // Account-level limits (keyed by the e-mail STRING, so they behave identically whether or not an account exists).
  const acct = h(b.email ?? ""); // sign-out has no e-mail: hashing `undefined` threw a TypeError (500) before the SDK ever ran
  if (action === "sign-in" || action === "verify") {
    const a = hit(`auth:acct:${action}:${acct}`, action === "verify" ? 6 : 8, 15 * 60_000);
    if (!a.allowed) return fail(429, "rate_limited", "Too many attempts. Try again later.", {}, { "retry-after": String(a.retryAfterS) });
  }
  if (action === "resend") {
    const a = hit(`auth:acct:resend:${acct}`, 1, 60_000);                       // one code per minute per address
    if (!a.allowed) return fail(429, "resend_too_soon", "Wait a minute before requesting another code.", {}, { "retry-after": String(a.retryAfterS) });
    const c = hit(`auth:acct:resend-hour:${acct}`, 5, 3_600_000);               // five per hour
    if (!c.allowed) return fail(429, "rate_limited", "Too many codes requested. Try again later.", {}, { "retry-after": String(c.retryAfterS) });
  }

  try {
    switch (action as Action) {
      case "sign-in": {
        const r = await call(() => auth.api.signInEmail({ body: { email: b.email, password: b.password }, headers, returnHeaders: true }));
        if (r.error) return out(action, classify.signIn(r.error), r.error);
        reset(`auth:acct:sign-in:${acct}`);
        return withCookies(json(200, { ok: true }), r.cookies);
      }
      case "sign-up": {
        const problem = passwordProblem(b.email, b.password);
        if (problem) return fail(422, "weak_password", problem);
        // No working mail transport = no way to deliver the verification code: refuse up front instead of creating an account nobody can verify.
        if (requireVerified() && !mailConfigured()) return out(action, classify.UPSTREAM);
        const r = await call(() => auth.api.signUpEmail({ body: { email: b.email, password: b.password, name: b.name ?? b.email.split("@")[0].slice(0, 64) }, headers, returnHeaders: true }));
        if (r.error) {
          const o = classify.signUp(r.error);
          if (o !== "accepted") return out(action, o, r.error);
        }
        // Same answer for a new and an existing address. With verification on, the code is e-mailed to the address that was entered.
        return json(200, { ok: true, needsVerify: requireVerified() });
      }
      case "verify": {
        const r = await call(() => auth.api.verifyEmailOTP({ body: { email: b.email, otp: b.code }, headers, returnHeaders: true }));
        if (r.error) return out(action, classify.verify(r.error), r.error);
        // autoSignInAfterVerification: Better Auth returns a session token and its Set-Cookie headers, forwarded here.
        return withCookies(json(200, { ok: true, signedIn: Boolean((r.data as { token?: string | null } | null)?.token) }), r.cookies);
      }
      case "resend": {
        const r = await call(() => auth.api.sendVerificationOTP({ body: { email: b.email, type: "email-verification" }, headers, returnHeaders: true }));
        if (r.error) {
          const o = classify.resend(r.error);
          if (o !== "accepted") return out(action, o, r.error);
        }
        return json(200, { ok: true });                                         // identical whether the account exists, is verified, or not
      }
      default: {
        const r = await call(() => auth.api.signOut({ headers, returnHeaders: true }));
        cacheClearAll();
        return withCookies(json(200, { ok: true }), r.cookies);
      }
    }
  } catch {
    console.warn(`[enigma.auth] action=${action} upstream_status=exception upstream_code=none`);
    return out(action, classify.UPSTREAM);
  }
}

/** GET /api/auth/me: the signed-in user (id, email, name, verified flag) or 401. Never returns a token. */
export async function GET(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action !== "me" || !authEnabled()) return fail(404, "not_found", "not found");
  const auth = await getAuth();
  if (!auth) return fail(503, "auth_unavailable", "authentication is not available");
  try {
    const s = await auth.api.getSession({ headers: req.headers });
    const u = s?.user;
    if (!u?.id) return fail(401, "unauthorized", "authentication required");
    return json(200, { id: `ba:${u.id}`, email: u.email ?? "", name: u.name ?? "", emailVerified: u.emailVerified === true });
  } catch {
    return fail(502, "auth_unavailable", "Authentication is temporarily unavailable.");
  }
}
