"use client";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/use-poll";
import type { AwsMetrics, AwsStatus } from "@/lib/aws-types";

/** AWS operations data. 60 s refresh at most (the backend also caches and floors AWS reads at 30 s); pauses while the tab is hidden. */
export const useAwsStatus = () => usePoll<AwsStatus>((s) => api("admin/aws/status", { signal: s }), 60_000, []);
export const useAwsMetrics = (range: string) => usePoll<AwsMetrics>((s) => api(`admin/aws/metrics?range=${range}`, { signal: s }), 60_000, [range]);
