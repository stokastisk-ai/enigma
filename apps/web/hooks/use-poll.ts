"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiFailure } from "@/lib/api";

export type PollState<T> = { data: T | null; error: ApiFailure | null; loading: boolean; updatedAt: number | null; stale: boolean; refresh: () => void };

/**
 * Poll a fetcher. Keeps the last good data on failure and flags it `stale` so the UI can say "showing data from 12s ago" instead of
 * silently presenting old numbers as current. Pauses while the tab is hidden. `deps` restart the poller (e.g. project / range change).
 */
export function usePoll<T>(fetcher: ((signal: AbortSignal) => Promise<T>) | null, intervalMs: number, deps: unknown[], nextIntervalMs?: (data: T | null) => number): PollState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const fetcherRef = useRef(fetcher);
  const nextRef = useRef(nextIntervalMs);
  const dataRef = useRef<T | null>(null);
  useEffect(() => { fetcherRef.current = fetcher; nextRef.current = nextIntervalMs; });          // keep the latest fetcher without restarting the poller
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!fetcherRef.current) { setLoading(false); return; }
    let cancelled = false;
    const ctl = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    const run = async () => {
      if (document.hidden) { timer = setTimeout(run, 1000); return; }
      try {
        const d = await fetcherRef.current!(ctl.signal);
        if (cancelled) return;
        dataRef.current = d; setData(d); setError(null); setUpdatedAt(Date.now());
      } catch (e) {
        if (cancelled || (e as Error).name === "AbortError") return;
        setError(e instanceof ApiFailure ? e : new ApiFailure(0, "unknown", "Unexpected error"));
      } finally {
        if (!cancelled) { setLoading(false); const ms = nextRef.current ? nextRef.current(dataRef.current) : intervalMs; if (ms > 0) timer = setTimeout(run, ms); }
      }
    };
    run();
    return () => { cancelled = true; ctl.abort(); clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, tick, ...deps]);

  const stale = error !== null && data !== null;
  return { data, error, loading, updatedAt, stale, refresh };
}
