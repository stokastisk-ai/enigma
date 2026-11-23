/**
 * Loopback self-rewrite guard (fixes `Failed to proxy http://localhost:3000/ ... socket hang up (ECONNRESET)` in `next dev -H 127.0.0.1`).
 *
 * Why it happens, step by step:
 *  1. Next's `NextURL` canonicalises every loopback IP (127.x.x.x, ::1) to the string `localhost`, so `request.url` is `http://localhost:3000/...`
 *     even when the server is bound to, and addressed as, 127.0.0.1.
 *  2. `@clerk/nextjs` answers a pass-through request with `x-middleware-rewrite: <request.url>` (same URL, plus decorated request headers).
 *  3. Next compares the rewrite's origin with the server's own origin (`http://127.0.0.1:3000`). They differ, so the rewrite is treated as an EXTERNAL
 *     destination and proxied to `http://localhost:3000/`, which re-enters the middleware and repeats until the socket is reset.
 *
 * The fix is deliberately narrow: only when the rewrite points at the very same URL as the request AND both hosts are loopback aliases, the rewrite
 * is converted into an ordinary "continue" (`x-middleware-next: 1`). Request-header overrides (`x-middleware-override-headers`, which carry Clerk's
 * auth state) are preserved. Any other rewrite — a different path, port, scheme or a non-loopback host — is returned untouched, so nothing here can
 * point a request at another host, and production (non-loopback) behaviour is identical to before.
 */
const LOOPBACK_HOST = /^(?:localhost|127(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}|\[::1\])$/i;

export const isLoopbackHostname = (hostname: string): boolean => LOOPBACK_HOST.test(hostname);

export function dropLoopbackSelfRewrite<T extends Response | null | undefined | void>(request: { url: string }, response: T): T {
  if (!response) return response;
  const res = response as Response;
  const rewrite = res.headers.get("x-middleware-rewrite");
  if (!rewrite) return response;
  let to: URL;
  let from: URL;
  try {
    to = new URL(rewrite);
    from = new URL(request.url);
  } catch {
    return response; // not parseable: leave it for Next to reject
  }
  const sameResource = to.protocol === from.protocol && to.port === from.port && to.pathname === from.pathname && to.search === from.search;
  if (!sameResource || !isLoopbackHostname(to.hostname) || !isLoopbackHostname(from.hostname)) return response;
  res.headers.delete("x-middleware-rewrite");
  res.headers.set("x-middleware-next", "1");
  return response;
}
