/** Framework-free core of the page guard, so the decision can be unit-tested without the Next.js runtime. */
export async function requireSessionOr<S>(lookup: () => Promise<S | null>, deny: () => never): Promise<S> {
  const session = await lookup(); // a lookup failure propagates: the page cannot render on an unverified identity
  if (!session) deny();
  return session as S;
}
