/**
 * Public (unauthenticated) page paths, matched natively instead of with Clerk's deprecated `createRouteMatcher`.
 *
 * This list is only a redirect optimisation for the proxy. It is NOT the authorization boundary: every protected layout and route handler calls
 * `requirePageSession()` / `getSession()` itself (Clerk's own guidance for removing path-based gates). Anything not listed is treated as protected,
 * so a path-normalisation surprise can only over-protect, never expose.
 */
const PUBLIC = /^\/(?:sign-in|sign-up)(?:\/[^/]*)*\/?$/;

export function isPublicPath(pathname: string): boolean {
  if (pathname === "/") return true;
  if (pathname.includes("\\") || pathname.includes("//") || pathname.includes("%")) return false; // ambiguous encodings are never "public"
  return PUBLIC.test(pathname);
}
