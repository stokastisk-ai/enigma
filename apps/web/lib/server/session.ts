import { createHmac } from "node:crypto";
import { cookies } from "next/headers";
import { devAuthAllowed } from "./env";
import { neonAuthEnabled, neonSession } from "./neon-auth";

export type Session = { userId: string; token: string; mode: "neon" | "dev" };

const b64 = (o: unknown) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

/** HS256 token for the local FastAPI in dev mode only (the API rejects HS256 unless ENIGMA_DEV_AUTH is on and not on a hosted platform/prod). */
export function mintDevToken(sub: string, secret: string): string {
  const head = b64({ alg: "HS256", typ: "JWT" });
  const body = b64({ sub, aud: "enigma-dev", exp: Math.floor(Date.now() / 1000) + 300 });
  const sig = createHmac("sha256", secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${sig}`;
}

/** Neon Managed Better Auth session -> Enigma session. The identity is namespaced `neon:<id>` (same value the API derives from the token's `sub`). */
async function neonCookieSession(): Promise<Session | null> {
  if (!neonAuthEnabled()) return null;
  const jar = await cookies();
  const r = await neonSession(jar.getAll().map((c) => c.name), (n) => jar.get(n)?.value);
  return r ? { userId: `neon:${r.user.id}`, token: r.token, mode: "neon" } : null;
}

export async function getSession(): Promise<Session | null> {
  const n = await neonCookieSession();
  if (n) return n;
  if (devAuthAllowed()) {
    // optional dev identity switch so E2E can prove tenant isolation with two users
    const jar = await cookies();
    const who = jar.get("enigma_dev_user")?.value ?? "";
    const userId = /^[a-z0-9_-]{3,40}$/.test(who) ? who : "dev-user-1";
    return { userId, token: mintDevToken(userId, process.env.ENIGMA_DEV_JWT_SECRET!), mode: "dev" };
  }
  return null;
}
