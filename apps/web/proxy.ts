import { clerkMiddleware } from "@clerk/nextjs/server";
import { NextResponse, type NextFetchEvent, type NextRequest } from "next/server";
import { COOKIE_ACCESS, COOKIE_REFRESH, cognitoConfig, pageRefreshUrl, verifyAccessToken } from "./lib/server/cognito";
import { dropLoopbackSelfRewrite } from "./lib/server/loopback-rewrite";
import { devAuthEnabled } from "./lib/server/hosted-env";
import { isPublicPath } from "./lib/server/public-paths";
import { hasNeonCookie, neonAuthEnabled } from "./lib/server/neon-auth";

const cognitoOn = process.env.ENIGMA_AUTH_PROVIDER === "cognito" && cognitoConfig() !== null;
const clerkConfigured = !cognitoOn && Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
const devAllowed = devAuthEnabled();
const neonOn = neonAuthEnabled();

// Clerk deprecated createRouteMatcher (path gates are not an authorization boundary). The path list is now a native, strict matcher used only to decide
// the early redirect; layouts and route handlers enforce the session themselves (lib/server/page-guard.ts, getSession()). clerkMiddleware stays: Clerk needs it.
const clerkBase = clerkMiddleware(async (auth, req) => {
  if (!isPublicPath(req.nextUrl.pathname)) await auth.protect();
});

// Clerk answers pass-through requests with a same-URL rewrite; behind `next dev -H 127.0.0.1` Next sees it as an external origin and proxies the request
// to itself until the socket resets (ECONNRESET). See lib/server/loopback-rewrite.ts.
const clerk = async (req: NextRequest, event: NextFetchEvent) => dropLoopbackSelfRewrite(req, await clerkBase(req, event));

/** Cognito: pages need a verified access token cookie (or a refresh cookie, which the BFF/session will exchange). API routes answer 401 themselves. */
async function cognito(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (isPublicPath(pathname) || pathname.startsWith("/api/auth/")) return NextResponse.next();
  const cfg = cognitoConfig()!;
  const at = req.cookies.get(COOKIE_ACCESS)?.value;
  if (at && (await verifyAccessToken(at, cfg))) return NextResponse.next();
  if (req.cookies.get(COOKIE_REFRESH)) return NextResponse.next(); // session refresh happens server-side; an invalid refresh token ends at the page gate
  // A Neon Managed Better Auth session cookie (only honoured while ENIGMA_NEON_AUTH=on). Presence is NOT trust: pages and the BFF verify it via getSession().
  if (neonOn && hasNeonCookie(req.cookies.getAll().map((c) => c.name))) return NextResponse.next();
  // no-store on every auth decision: a CDN must never cache "401" or "go sign in" and replay it to a different (signed-in) viewer.
  const noStore = { "cache-control": "no-store" };
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: { code: "unauthorized", message: "authentication required" } }, { status: 401, headers: noStore });
  // Behind CloudFront the request URL carries the Function URL host; redirect to the configured public origin so users never leave the CDN.
  // Pages never receive the /api-scoped refresh cookie, so try a refresh through /api/auth/refresh first; it falls back to /sign-in itself.
  const target = pageRefreshUrl(pathname) ?? "/sign-in";
  const redirect = NextResponse.redirect(new URL(target, process.env.ENIGMA_PUBLIC_ORIGIN || req.url));
  redirect.headers.set("cache-control", "no-store");
  return redirect;
}

/** Neon only (no Cognito/Clerk): pages need a Neon session cookie to get past the early redirect; layouts and the BFF verify it. */
function neonOnly(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (isPublicPath(pathname) || pathname.startsWith("/api/auth/")) return NextResponse.next();
  if (hasNeonCookie(req.cookies.getAll().map((c) => c.name))) return NextResponse.next();
  const noStore = { "cache-control": "no-store" };
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: { code: "unauthorized", message: "authentication required" } }, { status: 401, headers: noStore });
  const redirect = NextResponse.redirect(new URL("/sign-in?provider=neon", process.env.ENIGMA_PUBLIC_ORIGIN || req.url));
  redirect.headers.set("cache-control", "no-store");
  return redirect;
}

/** Fail closed: with no provider and no explicitly-allowed dev mode, every non-public route is refused. */
function devOrRefuse(req: Request) {
  if (devAllowed) return NextResponse.next();
  const { pathname } = new URL(req.url);
  if (isPublicPath(pathname)) return NextResponse.next();
  return new NextResponse("Authentication is not configured", { status: 503 });
}

export default cognitoOn ? cognito : clerkConfigured ? clerk : neonOn ? neonOnly : devOrRefuse;

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)", "/(api|trpc)(.*)"],
};
