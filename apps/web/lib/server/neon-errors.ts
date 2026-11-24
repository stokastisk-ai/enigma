import type { SdkError } from "./neon-auth.ts";

/**
 * Maps Neon Managed Better Auth errors to Enigma's public responses. Observed codes (probed against the live service): INVALID_EMAIL_OR_PASSWORD,
 * INVALID_ORIGIN, INVALID_OTP, PASSWORD_TOO_SHORT; documented: EMAIL_NOT_VERIFIED, USER_ALREADY_EXISTS, TOO_MANY_ATTEMPTS.
 * The public message never reveals whether an account exists and never echoes upstream text. Infrastructure problems (a wrong origin, an outage, a
 * network error) are NOT reported as bad credentials: they are `upstream` outcomes that the route logs by code only (no e-mail, password or code).
 */
export type Outcome = { status: number; code: string; message: string; needsVerify?: boolean; upstream?: boolean };

const TRY_LATER = "Too many attempts. Try again later.";
const norm = (e: SdkError | undefined) => ({ code: String(e?.code ?? "").toUpperCase(), status: e?.status ?? 0, msg: String(e?.message ?? "").toLowerCase() });

/** True for anything that is the service or its configuration failing rather than the user's input being wrong. */
export function isUpstream(e: SdkError | undefined): boolean {
  const { code, status } = norm(e);
  return status === 0 || status >= 500 || code === "INVALID_ORIGIN" || code.startsWith("NETWORK_") || code === "MISSING_OR_NULL_ORIGIN" || (status === 403 && code === "");
}

export const UPSTREAM: Outcome = { status: 502, code: "auth_unavailable", message: "Authentication is temporarily unavailable. Try again in a few minutes.", upstream: true };

export function signIn(e: SdkError | undefined): Outcome {
  const n = norm(e);
  if (n.code === "EMAIL_NOT_VERIFIED" || /not verified/.test(n.msg)) return { status: 403, code: "email_not_verified", message: "Verify your email with the code we send, then sign in.", needsVerify: true };
  if (n.status === 429) return { status: 429, code: "rate_limited", message: TRY_LATER };
  if (isUpstream(e)) return UPSTREAM;
  return { status: 401, code: "invalid_credentials", message: "Invalid email or password." };
}

export function signUp(e: SdkError | undefined): Outcome | "accepted" {
  const n = norm(e);
  if (n.status === 429) return { status: 429, code: "rate_limited", message: TRY_LATER };
  if (n.code === "PASSWORD_TOO_SHORT" || n.code === "PASSWORD_TOO_LONG") return { status: 422, code: "weak_password", message: "Use a password of 12 to 128 characters." };
  if (n.code === "INVALID_EMAIL") return { status: 422, code: "invalid_email", message: "Enter a valid email address." };
  // An existing address must look exactly like a new one (no account enumeration): the user is told to check their email or sign in.
  if (n.code.startsWith("USER_ALREADY_EXISTS") || n.status === 409 || n.status === 422) return "accepted";
  if (isUpstream(e)) return UPSTREAM;
  return { status: 422, code: "sign_up_failed", message: "Could not create the account. Check the details and try again." };
}

export function verify(e: SdkError | undefined): Outcome {
  const n = norm(e);
  if (n.status === 429 || n.code === "TOO_MANY_ATTEMPTS" || /too many/.test(n.msg)) return { status: 429, code: "too_many_attempts", message: "Too many wrong codes. Request a new code." };
  if (/expired/.test(n.msg) || n.code === "OTP_EXPIRED") return { status: 422, code: "expired_code", message: "That code has expired. Request a new one." };
  if (n.code === "EMAIL_ALREADY_VERIFIED" || /already verified/.test(n.msg)) return { status: 409, code: "already_verified", message: "This email is already verified. Sign in." };
  if (isUpstream(e)) return UPSTREAM;
  return { status: 422, code: "invalid_code", message: "That code is not valid. Check it or request a new one." };
}

/** Resend: only infrastructure failures are surfaced; "no such account" and "already verified" look identical to success. */
export function resend(e: SdkError | undefined): Outcome | "accepted" {
  const n = norm(e);
  if (n.status === 429) return { status: 429, code: "rate_limited", message: TRY_LATER };
  if (isUpstream(e)) return UPSTREAM;
  return "accepted";
}
