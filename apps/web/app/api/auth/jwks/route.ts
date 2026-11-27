import { NextResponse } from "next/server";
import { authEnabled, getAuth } from "@/lib/server/auth-server";

/**
 * GET /api/auth/jwks: the PUBLIC verification keys (Ed25519) of this deployment's self-hosted Better Auth. FastAPI fetches it over the private container
 * network (ENIGMA_AUTH_JWKS_URL) to verify the BFF's 15-minute tokens. Public keys only; private keys stay encrypted in the auth schema.
 */
export async function GET() {
  if (!authEnabled()) return NextResponse.json({ error: { code: "not_found", message: "not found" } }, { status: 404 });
  const auth = await getAuth();
  try {
    const jwks = await auth!.api.getJwks();
    return NextResponse.json(jwks, { headers: { "cache-control": "public, max-age=300" } });
  } catch {
    return NextResponse.json({ error: { code: "auth_unavailable", message: "authentication is temporarily unavailable" } }, { status: 503, headers: { "cache-control": "no-store" } });
  }
}
