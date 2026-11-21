"use client";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/use-poll";
import type { GpuMetrics, Overview, VllmMetrics } from "@/lib/types";
import { useConsole } from "./console-context";

export function useGpuMetrics(interval = 5000) {
  const c = useConsole();
  const qs = new URLSearchParams({ range: c.range, ...(c.runtime ? { runtime_id: c.runtime.id } : {}) });
  return usePoll<GpuMetrics>(c.project ? (s) => api(`projects/${c.project!.id}/metrics/gpu?${qs}`, { signal: s }) : null, interval, [c.project?.id, c.runtime?.id, c.range]);
}
export function useVllmMetrics(interval = 5000) {
  const c = useConsole();
  const qs = new URLSearchParams({ range: c.range, ...(c.runtime ? { runtime_id: c.runtime.id } : {}) });
  return usePoll<VllmMetrics>(c.project ? (s) => api(`projects/${c.project!.id}/metrics/vllm?${qs}`, { signal: s }) : null, interval, [c.project?.id, c.runtime?.id, c.range]);
}
export function useOverview(interval = 5000) {
  const c = useConsole();
  return usePoll<Overview>(c.project ? (s) => api(`projects/${c.project!.id}/overview`, { signal: s }) : null, interval, [c.project?.id]);
}
