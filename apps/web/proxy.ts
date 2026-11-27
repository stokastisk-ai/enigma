import { NextResponse, type NextRequest } from "next/server";
import { devAuthEnabled } from "./lib/server/hosted-env";
import { isPublicPath } from "./lib/server/public-paths";
import { authEnabled, hasAuthCookie } from "./lib/server/auth-server";

const devAllowed = devAuthEnabled();
const authOn = authEnabled();

/** Self-hosted Better Auth: pages need an Enigma session cookie to get past the early redirect; layouts and the BFF verify it (presence is NOT trust). */
function authOnly(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (isPublicPath(pathname) || pathname.startsWith("/api/auth/")) return NextResponse.next();
  if (hasAuthCookie(req.cookies.getAll().map((c) => c.name))) return NextResponse.next();
  // no-store on every auth decision: a CDN must never cache "401" or "go sign in" and replay it to a different (signed-in) viewer.
  const noStore = { "cache-control": "no-store" };
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: { code: "unauthorized", message: "authentication required" } }, { status: 401, headers: noStore });
  const redirect = NextResponse.redirect(new URL("/sign-in", process.env.ENIGMA_PUBLIC_ORIGIN || req.url));
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

export default authOn ? authOnly : devOrRefuse;

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)", "/(api|trpc)(.*)"],
};
