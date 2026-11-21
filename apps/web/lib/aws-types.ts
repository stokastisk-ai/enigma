/** Types for the admin-only AWS Infrastructure page (GET /admin/aws/*). AWS operations only: never GPU or vLLM data. */
export type ResourceStatus = "DEPLOYED" | "NOT_DEPLOYED" | "UNKNOWN" | "NOT_AUTHORIZED" | "SYNTHETIC";
export type AwsProvenance = { source: "none" | "synthetic" | "aws"; synthetic: boolean; mode: "off" | "mock" | "live"; region: string | null; environment: string; fetched_at: string; age_s: number; stale: boolean; ttl_s: number };
export type AwsResource = { key: string; service: string; name: string; status: ResourceStatus; detail: Record<string, string | number | boolean | string[] | null | undefined> };
export type AwsStatus = {
  mode: "off" | "mock" | "live";
  summary: { state: "NOT_DEPLOYED" | "DEPLOYED" | "PARTIAL" | "UNKNOWN" | "NOT_AUTHORIZED" | "SYNTHETIC"; message: string };
  app: { api: { status: string; version?: string; store?: string; uptime_s?: number }; build_sha: string | null; environment: string };
  resources: AwsResource[];
  topology: { id: string; label: string; kind: string; aws: boolean }[];
  topology_edges: [string, string][];
  cost: { mode: "link-out"; api_calls_made: boolean; links: Record<string, string>; credits: { remaining: string; expiry: string }; note: string };
  boundary: string;
  provenance: AwsProvenance;
};
export type AwsPoint = { ts: number; value: number };
export type AwsGroup = { state: "OK" | "NO_DATA"; series: Record<string, AwsPoint[]> };
export type AwsMetrics = { mode: string; range: string; period_s: number; window_s: number; groups: Record<string, AwsGroup>; state?: string; provenance: AwsProvenance };
