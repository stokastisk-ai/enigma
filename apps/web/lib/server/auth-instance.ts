import { betterAuth } from "better-auth";
import { emailOTP, jwt } from "better-auth/plugins";
import { Pool } from "pg";
import type { AuthConfig } from "./auth-server";
import { SESSION_DATA_TTL_S } from "./auth-server";
import { sendOtpEmail } from "./mailer";

/**
 * Self-hosted Better Auth (server only), loaded lazily by auth-server.ts. Tables live in schema `auth` (db/migrations/0004_auth_schema.sql) and are
 * reached through a login role that is a member of enigma_auth_app ONLY (it can never read tenant data). The catch-all Better Auth HTTP handler is
 * deliberately NOT mounted: the browser talks to Enigma's own strict, rate-limited routes (app/api/auth/[action]) and to the public JWKS route only.
 * Tokens for FastAPI: EdDSA (Ed25519), 15 minutes, iss = aud = the public origin, `sub` = user UUID, plus email/emailVerified claims.
 */
export function createAuth(cfg: AuthConfig) {
  const secure = cfg.origin.startsWith("https://");
  return betterAuth({
    appName: "Enigma",
    baseURL: cfg.origin,
    basePath: "/api/auth/ba",
    secret: cfg.secret,
    trustedOrigins: [cfg.origin],
    database: new Pool({ connectionString: cfg.dbDsn, max: 5, idleTimeoutMillis: 30_000, connectionTimeoutMillis: 5_000, options: "-c search_path=auth" }),
    emailAndPassword: {
      enabled: true,
      requireEmailVerification: cfg.requireVerified,
      minPasswordLength: 12,
      maxPasswordLength: 128,
      autoSignIn: false,
    },
    emailVerification: { autoSignInAfterVerification: true },
    session: {
      expiresIn: 7 * 24 * 3600,
      updateAge: 24 * 3600,
      cookieCache: { enabled: true, maxAge: SESSION_DATA_TTL_S },
    },
    rateLimit: { enabled: false },                     // Enigma's routes rate-limit per address and per account before calling Better Auth
    advanced: {
      cookiePrefix: "enigma-auth",
      useSecureCookies: secure,
      database: { generateId: "uuid" },
      ipAddress: { ipAddressHeaders: ["x-forwarded-for"] },   // set by the local reverse proxy only (it overwrites client-supplied values)
    },
    telemetry: { enabled: false },
    plugins: [
      emailOTP({
        otpLength: 6,
        expiresIn: 600,
        allowedAttempts: 5,
        overrideDefaultEmailVerification: true,
        sendVerificationOnSignUp: true,
        async sendVerificationOTP({ email, otp, type }) {
          await sendOtpEmail(email, otp, type);
        },
      }),
      jwt({
        jwks: { keyPairConfig: { alg: "EdDSA", crv: "Ed25519" } },
        jwt: {
          issuer: cfg.origin,
          audience: cfg.origin,
          expirationTime: "15m",
          definePayload: ({ user }) => ({ email: user.email, emailVerified: user.emailVerified === true, banned: user.banned === true }),
        },
      }),
    ],
  });
}

export type EnigmaAuth = ReturnType<typeof createAuth>;
