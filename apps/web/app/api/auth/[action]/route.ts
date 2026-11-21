import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { COOKIE_REFRESH, cognitoConfig, confirmSignUp, publicError, revoke, sameOrigin, signIn, signUp } from "@/lib/server/cognito";
import { cognitoSelected } from "@/lib/server/env";
import { clearAuthCookies, setAuthCookies } from "@/lib/server/session";
import { cookies } from "next/headers";

/**
 * Cognito auth endpoints (sign-in, sign-up, confirm, sign-out). Only active when Cognito is selected; otherwise 404.
 * Same-origin POSTs only (CSRF), generic error messages (no account enumeration), tokens only ever in httpOnly cookies.
 */
const Email = z.string().trim().toLowerCase().email().max(254);
const Password = z.string().min(8).max(256);
const BODIES = {
  "sign-in": z.object({ email: Email, password: Password }).strict(),
  "sign-up": z.object({ email: Email, password: Password }).strict(),
  confirm: z.object({ email: Email, code: z.string().regex(/^\d{6}$/) }).strict(),
  "sign-out": z.object({}).strict(),
} as const;

const json = (status: number, body: unknown) => NextResponse.json(body, { status, headers: { "cache-control": "no-store" } });

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  const cfg = cognitoConfig();
  if (!cognitoSelected() || !cfg || !(action in BODIES)) return json(404, { error: { code: "not_found", message: "not found" } });
  if (!sameOrigin(req.headers.get("origin"), req.headers.get("host"), process.env.ENIGMA_PUBLIC_HOST))
    return json(403, { error: { code: "bad_origin", message: "cross-origin request refused" } });
  if (Number(req.headers.get("content-length") ?? 0) > 4096) return json(413, { error: { code: "payload_too_large", message: "body too large" } });

  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return json(422, { error: { code: "invalid_json", message: "body is not valid JSON" } });
  }
  const parsed = BODIES[action as keyof typeof BODIES].safeParse(raw);
  if (!parsed.success) return json(422, { error: { code: "validation_error", message: "Check the email, password (min 8) and code format." } });
  const b = parsed.data as { email: string; password: string; code: string };

  try {
    switch (action) {
      case "sign-in": {
        await setAuthCookies(await signIn(cfg, b.email, b.password));
        return json(200, { ok: true });
      }
      case "sign-up": {
        await signUp(cfg, b.email, b.password);
        return json(200, { ok: true, needsConfirm: true });
      }
      case "confirm": {
        await confirmSignUp(cfg, b.email, b.code);
        return json(200, { ok: true });
      }
      default: {
        const rt = (await cookies()).get(COOKIE_REFRESH)?.value;
        if (rt) await revoke(cfg, rt);
        await clearAuthCookies();
        return json(200, { ok: true });
      }
    }
  } catch (e) {
    const p = publicError(e);
    return json(p.status, { error: { code: p.code, message: p.message }, needsConfirm: p.needsConfirm ?? false });
  }
}
