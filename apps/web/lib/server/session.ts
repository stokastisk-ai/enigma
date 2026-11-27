import { createHmac } from "node:crypto";
import { cookies, headers } from "next/headers";
import { devAuthAllowed } from "./env";
import { authEnabled, authSession } from "./auth-server";

export type Session = { userId: string; token: string; mode: "betterauth" | "dev" };

const b64 = (o: unknown) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

/** HS256 token for the local FastAPI in dev mode only (the API rejects HS256 unless ENIGMA_DEV_AUTH is on and not on a hosted platform/prod). */
export function mintDevToken(sub: string, secret: string): string {
  const head = b64({ alg: "HS256", typ: "JWT" });
  const body = b64({ sub, aud: "enigma-dev", exp: Math.floor(Date.now() / 1000) + 300 });
  const sig = createHmac("sha256", secret).update(`${head}.${body}`).digest("base64url");
  return `${head}.${body}.${sig}`;
}

/** Self-hosted Better Auth session -> Enigma session. The identity is namespaced `ba:<id>` (same value the API derives from the token's `sub`). */
async function authCookieSession(): Promise<Session | null> {
  if (!authEnabled()) return null;
  const jar = await cookies();
  const r = await authSession(await headers(), jar.getAll().map((c) => c.name), (n) => jar.get(n)?.value);
  return r ? { userId: `ba:${r.user.id}`, token: r.token, mode: "betterauth" } : null;
}

export async function getSession(): Promise<Session | null> {
  const n = await authCookieSession();
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
