import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";
import { COOKIE_ACCESS, COOKIE_REFRESH, cognitoConfig, verifyAccessToken } from "./lib/server/cognito";

const cognitoOn = process.env.ENIGMA_AUTH_PROVIDER === "cognito" && cognitoConfig() !== null;
const clerkConfigured = !cognitoOn && Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
const devAllowed =
  process.env.ENIGMA_DEV_AUTH === "1" &&
  !process.env.VERCEL &&
  !process.env.VERCEL_ENV &&
  !process.env.AWS_EXECUTION_ENV &&
  !process.env.AWS_LAMBDA_FUNCTION_NAME &&
  (process.env.ENIGMA_DEV_JWT_SECRET ?? "").length >= 32;
const isPublic = createRouteMatcher(["/", "/sign-in(.*)", "/sign-up(.*)"]);

const clerk = clerkMiddleware(async (auth, req) => {
  if (!isPublic(req)) await auth.protect();
});

/** Cognito: pages need a verified access token cookie (or a refresh cookie, which the BFF/session will exchange). API routes answer 401 themselves. */
async function cognito(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (pathname === "/" || pathname.startsWith("/sign-") || pathname.startsWith("/api/auth/")) return NextResponse.next();
  const cfg = cognitoConfig()!;
  const at = req.cookies.get(COOKIE_ACCESS)?.value;
  if (at && (await verifyAccessToken(at, cfg))) return NextResponse.next();
  if (req.cookies.get(COOKIE_REFRESH)) return NextResponse.next(); // session refresh happens server-side; an invalid refresh token ends at the page gate
  if (pathname.startsWith("/api/")) return NextResponse.json({ error: { code: "unauthorized", message: "authentication required" } }, { status: 401 });
  // Behind CloudFront the request URL carries the Function URL host; redirect to the configured public origin so users never leave the CDN.
  return NextResponse.redirect(new URL("/sign-in", process.env.ENIGMA_PUBLIC_ORIGIN || req.url));
}

/** Fail closed: with no provider and no explicitly-allowed dev mode, every non-public route is refused. */
function devOrRefuse(req: Request) {
  if (devAllowed) return NextResponse.next();
  const { pathname } = new URL(req.url);
  if (pathname === "/" || pathname.startsWith("/sign-")) return NextResponse.next();
  return new NextResponse("Authentication is not configured", { status: 503 });
}

export default cognitoOn ? cognito : clerkConfigured ? clerk : devOrRefuse;

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)", "/(api|trpc)(.*)"],
};
