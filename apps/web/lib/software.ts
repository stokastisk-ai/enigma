/** Formatting for the connector's SELF-REPORTED software identity. Missing or null values read "Not reported", never the raw word "null". */
export type SoftwareRow = { key: string; label: string; value: string | null; title?: string };

const ORDER: ReadonlyArray<readonly [string, string]> = [
  ["stokastisk_version", "Stokastisk"],
  ["stokastisk_build", "build"],
  ["vllm_version", "vLLM"],
  ["python_version", "Python"],
  ["driver_version", "NVIDIA driver"],
  ["wheel_sha256", "wheel SHA-256"],
];

const clean = (v: unknown): string | null => (typeof v === "string" && v.trim() !== "" && v.trim().toLowerCase() !== "null" ? v.trim() : null);

export function softwareRows(identity: Record<string, unknown> | null | undefined): SoftwareRow[] {
  const src = identity ?? {};
  const rows: SoftwareRow[] = ORDER.map(([key, label]) => {
    const value = clean(src[key]);
    return key === "wheel_sha256" && value ? { key, label, value: `${value.slice(0, 12)}…`, title: value } : { key, label, value };
  });
  for (const [key, raw] of Object.entries(src)) {
    if (ORDER.some(([k]) => k === key)) continue;
    const value = clean(raw);
    if (value) rows.push({ key, label: key.replace(/_/g, " "), value });
  }
  return rows;
}

export const softwareText = (identity: Record<string, unknown> | null | undefined): string =>
  softwareRows(identity).map((r) => `${r.label} ${r.value ?? "not reported"}`).join(" · ");
