import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { sameOrigin } from "@/lib/server/cognito";
import * as classify from "@/lib/server/neon-errors";
import { cacheClearAll, getNeonAuth, neonAuthEnabled, neonRequireVerified, type SdkError } from "@/lib/server/neon-auth";
import { passwordProblem } from "@/lib/server/password-policy";
import { clientIp, hit, reset } from "@/lib/server/rate-limit";

/**
 * Neon Managed Better Auth endpoints (sign-in, sign-up, verify, resend, sign-out, me). 404 unless ENIGMA_NEON_AUTH=on and configured, so this code changes
 * nothing for Cognito users. Same-origin POSTs only (CSRF; the SDK's session cookies are SameSite as a second layer), strict bodies, generic errors (no
 * account enumeration), rate limits per client address and per account, and no token ever reaches the browser: the SDK sets httpOnly cookies.
 * Failures are logged by action + status + error code ONLY (never an e-mail address, password, code or cookie), so a delivery or configuration problem can
 * be told apart from bad input in the Railway logs.
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
  console.warn(`[enigma.neon-auth] action=${action} upstream_status=${e?.status ?? "none"} upstream_code=${String(e?.code ?? "none").replace(/[^A-Za-z0-9_]/g, "").slice(0, 48)}`);
}
function out(action: string, o: classify.Outcome, error?: SdkError | null) {
  if (o.upstream) logUpstream(action, error ?? undefined);
  const headers: Record<string, string> = o.status === 429 ? { "retry-after": "60" } : {};
  return fail(o.status, o.code, o.message, o.needsVerify ? { needsVerify: true } : {}, headers);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (!neonAuthEnabled() || !(action in BODIES)) return fail(404, "not_found", "not found");
  if (!sameOrigin(req.headers.get("origin"), req.headers.get("host"), process.env.ENIGMA_PUBLIC_HOST)) return fail(403, "bad_origin", "cross-origin request refused");
  if (Number(req.headers.get("content-length") ?? 0) > 4096) return fail(413, "payload_too_large", "body too large");

  const ip = hit(`neon:ip:${h(clientIp(req.headers))}`, 30, 5 * 60_000);
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

  const auth = await getNeonAuth();
  if (!auth) return fail(503, "auth_unavailable", "authentication is not available");

  // Account-level limits (keyed by the e-mail STRING, so they behave identically whether or not an account exists).
  const acct = h(b.email);
  if (action === "sign-in" || action === "verify") {
    const a = hit(`neon:acct:${action}:${acct}`, action === "verify" ? 6 : 8, 15 * 60_000);
    if (!a.allowed) return fail(429, "rate_limited", "Too many attempts. Try again later.", {}, { "retry-after": String(a.retryAfterS) });
  }
  if (action === "resend") {
    const a = hit(`neon:acct:resend:${acct}`, 1, 60_000);                       // one code per minute per address
    if (!a.allowed) return fail(429, "resend_too_soon", "Wait a minute before requesting another code.", {}, { "retry-after": String(a.retryAfterS) });
    const c = hit(`neon:acct:resend-hour:${acct}`, 5, 3_600_000);               // five per hour
    if (!c.allowed) return fail(429, "rate_limited", "Too many codes requested. Try again later.", {}, { "retry-after": String(c.retryAfterS) });
  }

  try {
    switch (action as Action) {
      case "sign-in": {
        const { error } = await auth.signIn.email({ email: b.email, password: b.password });
        if (error) return out(action, classify.signIn(error), error);
        reset(`neon:acct:sign-in:${acct}`);
        return json(200, { ok: true });
      }
      case "sign-up": {
        const problem = passwordProblem(b.email, b.password);
        if (problem) return fail(422, "weak_password", problem);
        const { error } = await auth.signUp.email({ email: b.email, password: b.password, name: b.name ?? b.email.split("@")[0].slice(0, 64) });
        if (error) {
          const o = classify.signUp(error);
          if (o !== "accepted") return out(action, o, error);
        }
        // Same answer for a new and an existing address. With verification on, the code is sent by Neon to the address that was entered.
        return json(200, { ok: true, needsVerify: neonRequireVerified() });
      }
      case "verify": {
        const { data, error } = await auth.emailOtp.verifyEmail({ email: b.email, otp: b.code });
        if (error) return out(action, classify.verify(error), error);
        // With "auto sign-in after verification" Neon returns a session token and the SDK has already set the session cookies.
        return json(200, { ok: true, signedIn: Boolean(data?.token) });
      }
      case "resend": {
        const origin = (process.env.ENIGMA_PUBLIC_ORIGIN ?? "").replace(/\/$/, "");
        const { error } = await auth.sendVerificationEmail({ email: b.email, ...(origin ? { callbackURL: `${origin}/sign-in?provider=neon` } : {}) });
        if (error) {
          const o = classify.resend(error);
          if (o !== "accepted") return out(action, o, error);
        }
        return json(200, { ok: true });                                         // identical whether the account exists, is verified, or not
      }
      default: {
        await auth.signOut();
        cacheClearAll();
        return json(200, { ok: true });
      }
    }
  } catch {
    console.warn(`[enigma.neon-auth] action=${action} upstream_status=exception upstream_code=none`);
    return out(action, classify.UPSTREAM);
  }
}

/** GET /api/auth/neon/me: the signed-in Neon user (id, email, name, verified flag) or 401. Never returns a token. */
export async function GET(_req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action !== "me" || !neonAuthEnabled()) return fail(404, "not_found", "not found");
  const auth = await getNeonAuth();
  if (!auth) return fail(503, "auth_unavailable", "authentication is not available");
  try {
    const { data: s } = await auth.getSession();
    const u = s?.user;
    if (!u?.id) return fail(401, "unauthorized", "authentication required");
    return json(200, { id: `neon:${u.id}`, email: u.email ?? "", name: u.name ?? "", emailVerified: u.emailVerified === true });
  } catch {
    return fail(502, "auth_unavailable", "Authentication is temporarily unavailable.");
  }
}
