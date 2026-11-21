import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

const configured = Boolean(process.env.CLERK_SECRET_KEY && process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
const devAllowed =
  process.env.ENIGMA_DEV_AUTH === "1" && !process.env.VERCEL && !process.env.VERCEL_ENV && (process.env.ENIGMA_DEV_JWT_SECRET ?? "").length >= 32;
const isPublic = createRouteMatcher(["/", "/sign-in(.*)", "/sign-up(.*)"]);

const clerk = clerkMiddleware(async (auth, req) => {
  if (!isPublic(req)) await auth.protect();
});

/** Fail closed: with neither Clerk nor an explicitly-allowed dev mode, every non-public route is refused. */
function devOrRefuse(req: Request) {
  if (devAllowed) return NextResponse.next();
  const { pathname } = new URL(req.url);
  if (pathname === "/" || pathname.startsWith("/sign-")) return NextResponse.next();
  return new NextResponse("Authentication is not configured", { status: 503 });
}

export default configured ? clerk : devOrRefuse;

export const config = {
  matcher: ["/((?!_next|.*\\..*).*)", "/(api|trpc)(.*)"],
};
