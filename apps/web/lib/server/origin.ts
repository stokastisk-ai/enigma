/** CSRF: state-changing requests must be same-origin. */
export function sameOrigin(originHeader: string | null, hostHeader: string | null, publicHost?: string | null): boolean {
  if (!originHeader) return false; // browsers always send Origin on cross-site and same-site POSTs; absence means a non-browser or stripped request
  try {
    const o = new URL(originHeader);
    // Allowed: the request's own Host, plus publicHost (ENIGMA_PUBLIC_HOST), the operator-configured public hostname.
    // X-Forwarded-Host is deliberately NOT trusted: it is client-controlled unless a trusted proxy overwrites it.
    const hosts = [hostHeader, publicHost].filter(Boolean) as string[];
    return hosts.includes(o.host);
  } catch {
    return false;
  }
}
