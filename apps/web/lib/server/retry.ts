/**
 * Bounded retries for SAFE operations only (reads, or writes that carry an idempotency key). Railway Serverless puts an idle service to sleep, so the first
 * request after a quiet period can hit a container that is still starting; a short, bounded wait turns that into a slower first response instead of an error.
 * Never use this for a non-idempotent write: after an ambiguous failure the write may already have happened.
 */
export type RetryOptions = {
  /** total attempts including the first (>= 1) */
  attempts: number;
  /** delay before attempt n+1, in ms (index 0 = after the first failure); the last value repeats */
  delaysMs: readonly number[];
  sleep?: (ms: number) => Promise<void>;
};

const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Runs `fn`; `retryable(result)` says whether a (non-throwing) result should be retried, and a thrown error is retried when `retryOnThrow` is true. */
export async function withRetries<T>(
  fn: (attempt: number) => Promise<T>,
  retryable: (result: T) => boolean,
  opts: RetryOptions,
  retryOnThrow = true,
): Promise<T> {
  const sleep = opts.sleep ?? realSleep;
  const max = Math.max(1, Math.floor(opts.attempts));
  let lastErr: unknown;
  for (let attempt = 1; attempt <= max; attempt++) {
    try {
      const r = await fn(attempt);
      if (attempt === max || !retryable(r)) return r;
    } catch (e) {
      lastErr = e;
      if (!retryOnThrow || attempt === max) throw e;
    }
    const d = opts.delaysMs[Math.min(attempt - 1, opts.delaysMs.length - 1)] ?? 0;
    await sleep(d);
  }
  throw lastErr ?? new Error("unreachable");
}

/** A response that means "the container behind this is not ready", never "your request was wrong". */
export const isTransientStatus = (s: number) => s === 502 || s === 503 || s === 504;
