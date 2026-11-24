import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { sameOrigin } from "@/lib/server/cognito";
import { cacheClearAll, getNeonAuth, neonAuthEnabled, neonRequireVerified, type SdkError } from "@/lib/server/neon-auth";
import { clientIp, hit, reset } from "@/lib/server/rate-limit";

/**
 * Neon Managed Better Auth endpoints (sign-in, sign-up, verify, sign-out). 404 unless ENIGMA_NEON_AUTH=on and configured, so shipping this code changes
 * nothing for Cognito users. Same-origin POSTs only (CSRF; the SDK's session cookies are SameSite as a second layer), strict bodies, generic errors
 * (no account enumeration), rate limits per client address and per account, and no token ever reaches the browser: the SDK sets httpOnly cookies.
 */
const Email = z.string().trim().toLowerCase().email().max(254);
// Sign-up enforces a stronger policy than Better Auth's default (8). Sign-in only bounds the length so accounts created elsewhere can still sign in.
const NewPassword = z.string().min(12).max(128);
const AnyPassword = z.string().min(1).max(128);
const BODIES = {
  "sign-in": z.object({ email: Email, password: AnyPassword }).strict(),
  "sign-up": z.object({ email: Email, password: NewPassword, name: z.string().trim().min(1).max(64).optional() }).strict(),
  verify: z.object({ email: Email, code: z.string().regex(/^\d{6}$/) }).strict(),
  "sign-out": z.object({}).strict(),
} as const;
type Action = keyof typeof BODIES;

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  NextResponse.json(body, { status, headers: { "cache-control": "no-store", ...headers } });
const fail = (status: number, code: string, message: string, extra: Record<string, unknown> = {}, headers: Record<string, string> = {}) =>
  json(status, { error: { code, message }, ...extra }, headers);
const h = (s: string) => createHash("sha256").update(s).digest("hex").slice(0, 32);

const isUnverified = (e: SdkError) => e?.code === "EMAIL_NOT_VERIFIED" || /not verified/i.test(e?.message ?? "");

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
  if (!parsed.success)
    return fail(422, "validation_error", action === "sign-up" ? "Check the email, and use a password of 12 to 128 characters." : "Check the email, password and code format.");
  const b = parsed.data as { email: string; password: string; code: string; name?: string };

  const auth = await getNeonAuth();
  if (!auth) return fail(503, "auth_unavailable", "authentication is not available");

  // Account-level limits: sign-in/verify guesses are capped per account regardless of source address.
  const accountKey = action === "sign-in" || action === "verify" ? `neon:acct:${action}:${h(b.email)}` : null;
  if (accountKey) {
    const a = hit(accountKey, action === "verify" ? 6 : 8, 15 * 60_000);
    if (!a.allowed) return fail(429, "rate_limited", "Too many attempts. Try again later.", {}, { "retry-after": String(a.retryAfterS) });
  }

  try {
    switch (action as Action) {
      case "sign-in": {
        const { error } = await auth.signIn.email({ email: b.email, password: b.password });
        if (isUnverified(error)) return fail(403, "email_not_verified", "Verify your email with the code we sent, then sign in.", { needsVerify: true });
        if (error) return fail(error.status === 429 ? 429 : 401, error.status === 429 ? "rate_limited" : "invalid_credentials", error.status === 429 ? "Too many attempts. Try again later." : "Invalid email or password.");
        reset(accountKey!);
        return json(200, { ok: true });
      }
      case "sign-up": {
        const { error } = await auth.signUp.email({ email: b.email, password: b.password, name: b.name ?? b.email.split("@")[0].slice(0, 64) });
        // An existing address and a fresh one answer the same way, so this endpoint does not reveal which e-mails have accounts.
        if (error && error.status === 429) return fail(429, "rate_limited", "Too many attempts. Try again later.");
        if (error && error.status !== 422 && error.status !== 400 && error.status !== 409) return fail(502, "auth_unavailable", "Could not create the account right now.");
        if (error) return fail(422, "sign_up_failed", "Could not create the account. If you already have one, sign in.");
        return json(200, { ok: true, needsVerify: neonRequireVerified() });
      }
      case "verify": {
        const { error } = await auth.emailOtp.verifyEmail({ email: b.email, otp: b.code });
        if (error) return fail(error.status === 429 ? 429 : 422, error.status === 429 ? "rate_limited" : "invalid_code", error.status === 429 ? "Too many attempts. Try again later." : "That code is invalid or expired.");
        return json(200, { ok: true });
      }
      default: {
        await auth.signOut();
        cacheClearAll();
        return json(200, { ok: true });
      }
    }
  } catch {
    return fail(502, "auth_unavailable", "Authentication is temporarily unavailable.");
  }
}

/** GET /api/auth/neon/me: the signed-in Neon user (id, email, name, verified flag) or 401. Never returns a token. */
export async function GET(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
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
