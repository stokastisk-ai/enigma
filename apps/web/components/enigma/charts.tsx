"use client";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { GpuPoint, Hist, VllmPoint } from "@/lib/types";
import { EmptyState } from "./status";

export type Row = { ts: number } & Record<string, number | null>;
export type SeriesDef = { key: string; label: string; color: string; dashed?: boolean };
export const S1 = "var(--series-1)", S2 = "var(--series-2)", S3 = "var(--series-3)";

export const fmtClock = (ms: number, utc: boolean, withSeconds = true) =>
  new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}), hour12: false, timeZone: utc ? "UTC" : undefined });

/** Merge per-GPU points into one row per timestamp: { ts, g0: v, g1: v }. Missing values stay null (rendered as a gap, never 0). */
export function mergeGpu(series: Record<string, GpuPoint[]> | undefined, field: keyof GpuPoint): Row[] {
  const rows = new Map<number, Row>();
  for (const [idx, pts] of Object.entries(series ?? {})) {
    for (const p of pts) {
      const ts = Date.parse(p.t);
      const row = rows.get(ts) ?? ({ ts } as Row);
      row[`g${idx}`] = (p[field] as number | null) ?? null;
      rows.set(ts, row);
    }
  }
  return [...rows.values()].sort((a, b) => a.ts - b.ts);
}

export const gpuSeries = (series: Record<string, unknown> | undefined): SeriesDef[] =>
  Object.keys(series ?? {}).sort().map((i) => ({ key: `g${i}`, label: `GPU ${i}`, color: [S1, S2, S3][Number(i)] ?? S3 }));

export function vllmRows(points: VllmPoint[], pick: Partial<Record<string, (p: VllmPoint) => number | null>>): Row[] {
  return points.map((p) => {
    const r = { ts: Date.parse(p.t) } as Row;
    for (const [k, fn] of Object.entries(pick)) r[k] = fn!(p);
    return r;
  });
}

function DataTableView({ rows, series, unit, utc }: { rows: Row[]; series: SeriesDef[]; unit: string; utc: boolean }) {
  const shown = rows.slice(-30);
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground">View as table</summary>
      <div className="mt-2 max-h-48 overflow-auto rounded-md border">
        <table className="w-full tabular">
          <thead className="sticky top-0 bg-card text-left text-muted-foreground"><tr><th className="p-1.5">Time</th>{series.map((s) => <th key={s.key} className="p-1.5">{s.label} ({unit})</th>)}</tr></thead>
          <tbody>{shown.map((r) => <tr key={r.ts} className="border-t"><td className="p-1.5">{fmtClock(r.ts, utc)}</td>{series.map((s) => <td key={s.key} className="p-1.5">{r[s.key] == null ? "N/A" : (r[s.key] as number).toFixed(2)}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </details>
  );
}

export function ChartCard({ title, description, right, children, testId }: { title: string; description?: string; right?: React.ReactNode; children: React.ReactNode; testId?: string }) {
  return (
    <Card data-testid={testId} className="min-w-0">
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="min-w-0"><CardTitle className="text-sm">{title}</CardTitle>{description ? <CardDescription className="text-xs">{description}</CardDescription> : null}</div>
        {right}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

type TSProps = { rows: Row[]; series: SeriesDef[]; unit: string; utc: boolean; domain?: [number | "auto", number | "auto"]; area?: boolean; height?: number; format?: (v: number) => string; label: string; empty?: string };

export function TimeSeries({ rows, series, unit, utc, domain, area, height = 200, format, label, empty = "No samples in this time range." }: TSProps) {
  const hasData = rows.some((r) => series.some((s) => r[s.key] != null));
  if (!hasData) return <EmptyState title="No data">{empty}</EmptyState>;
  const config = Object.fromEntries(series.map((s) => [s.key, { label: s.label, color: s.color }])) as ChartConfig;
  const f = format ?? ((v: number) => v.toFixed(1));
  const axes = (
    <>
      <CartesianGrid vertical={false} strokeOpacity={0.15} />
      <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={(v) => fmtClock(v, utc, false)} tickLine={false} axisLine={false} minTickGap={48} tick={{ fontSize: 11 }} />
      <YAxis width={44} domain={domain ?? [0, "auto"]} tickLine={false} axisLine={false} tickFormatter={(v) => f(v)} tick={{ fontSize: 11 }} />
      <ChartTooltip cursor={{ strokeOpacity: 0.4 }} content={<ChartTooltipContent labelFormatter={(_, p) => fmtClock(p?.[0]?.payload?.ts ?? 0, utc)} formatter={(v, name) => <span className="tabular flex w-full justify-between gap-3"><span className="text-muted-foreground">{config[name as string]?.label ?? name}</span><span>{typeof v === "number" ? `${f(v)} ${unit}` : "N/A"}</span></span>} />} />
      {series.length > 1 ? <ChartLegend content={<ChartLegendContent />} /> : null}
    </>
  );
  return (
    <div>
      {/* only the graphic is an image: the table disclosure below is interactive and must not be nested inside role="img" (axe: nested-interactive) */}
      <div role="img" aria-label={`${label}: ${series.map((s) => s.label).join(", ")} over time`}>
      <ChartContainer config={config} className="w-full" style={{ height }}>
        {area ? (
          <AreaChart data={rows} margin={{ left: 0, right: 8, top: 4 }}>{axes}
            {series.map((s) => <Area key={s.key} dataKey={s.key} name={s.key} type="monotone" stroke={s.color} fill={s.color} fillOpacity={0.15} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />)}
          </AreaChart>
        ) : (
          <LineChart data={rows} margin={{ left: 0, right: 8, top: 4 }}>{axes}
            {series.map((s) => <Line key={s.key} dataKey={s.key} name={s.key} type="monotone" stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined} dot={false} activeDot={{ r: 4, stroke: "var(--card)", strokeWidth: 2 }} isAnimationActive={false} connectNulls={false} />)}
          </LineChart>
        )}
      </ChartContainer>
      </div>
      <DataTableView rows={rows} series={series} unit={unit} utc={utc} />
    </div>
  );
}

export function Sparkline({ rows, k, color = S1, label }: { rows: Row[]; k: string; color?: string; label: string }) {
  if (!rows.some((r) => r[k] != null)) return <div className="h-8 text-xs text-muted-foreground">no data</div>;
  return (
    <div role="img" aria-label={`${label} trend`}>
      <ChartContainer config={{ [k]: { label, color } }} className="h-8 w-full aspect-auto">
        <AreaChart data={rows} margin={{ left: 0, right: 0, top: 2, bottom: 0 }}>
          <Area dataKey={k} type="monotone" stroke={color} fill={color} fillOpacity={0.12} strokeWidth={1.5} dot={false} isAnimationActive={false} connectNulls={false} />
        </AreaChart>
      </ChartContainer>
    </div>
  );
}

/** Per-GPU VRAM: used vs free, stacked, with the 2px surface gap between segments. */
export function VramBars({ gpus }: { gpus: { name: string; used: number | null; total: number | null }[] }) {
  const known = gpus.filter((g) => g.used != null && g.total != null);
  if (!known.length) return <EmptyState title="VRAM not reported">No memory readings are available for this runtime.</EmptyState>;
  const data = known.map((g) => ({ name: g.name, used: g.used! / 1024, free: Math.max(g.total! - g.used!, 0) / 1024 }));
  const config = { used: { label: "Used", color: S1 }, free: { label: "Free", color: "var(--muted)" } } satisfies ChartConfig;
  return (
    <div role="img" aria-label={`VRAM used versus free per GPU, ${data.map((d) => `${d.name} ${d.used.toFixed(1)} GiB used`).join(", ")}`}>
      <ChartContainer config={config} className="w-full" style={{ height: 40 + data.length * 44 }}>
        <BarChart data={data} layout="vertical" margin={{ left: 0, right: 8 }}>
          <CartesianGrid horizontal={false} strokeOpacity={0.15} />
          <XAxis type="number" tickLine={false} axisLine={false} unit=" GiB" tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="name" width={56} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
          <ChartTooltip content={<ChartTooltipContent formatter={(v, n) => <span className="tabular">{config[n as "used" | "free"]?.label}: {(v as number).toFixed(2)} GiB</span>} />} />
          <ChartLegend content={<ChartLegendContent />} />
          <Bar dataKey="used" stackId="v" fill="var(--color-used)" stroke="var(--card)" strokeWidth={2} radius={[4, 0, 0, 4]} isAnimationActive={false} />
          <Bar dataKey="free" stackId="v" fill="var(--color-free)" stroke="var(--card)" strokeWidth={2} radius={[0, 4, 4, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
    </div>
  );
}

/** Cumulative Prometheus histogram -> per-bucket counts. */
export function HistogramBars({ hist, label, unitScale = 1000, unit = "ms" }: { hist: Hist | null | undefined; label: string; unitScale?: number; unit?: string }) {
  if (!hist || hist.count <= 0) return <EmptyState title="No observations yet">Histogram buckets fill as requests complete.</EmptyState>;
  const data = hist.cum.map((c, i) => {
    const prev = i === 0 ? 0 : hist.cum[i - 1];
    const lo = i === 0 ? 0 : hist.le[i - 1];
    const name = i < hist.le.length ? `≤${+(hist.le[i] * unitScale).toPrecision(3)}` : `>${+(hist.le[hist.le.length - 1] * unitScale).toPrecision(3)}`;
    return { name, count: c - prev, lo };
  });
  const config = { count: { label: "Requests", color: S1 } } satisfies ChartConfig;
  return (
    <div role="img" aria-label={`${label} histogram in ${unit}`}>
      <ChartContainer config={config} className="w-full" style={{ height: 200 }}>
        <BarChart data={data} margin={{ left: 0, right: 8, top: 4 }}>
          <CartesianGrid vertical={false} strokeOpacity={0.15} />
          <XAxis dataKey="name" tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={14} tick={{ fontSize: 10 }} />
          <YAxis width={36} allowDecimals={false} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
          <ChartTooltip content={<ChartTooltipContent formatter={(v) => <span className="tabular">{v as number} requests</span>} labelFormatter={(l) => `${l} ${unit}`} />} />
          <Bar dataKey="count" fill="var(--color-count)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ChartContainer>
      <p className="mt-1 text-xs text-muted-foreground tabular">Cumulative since server start · {hist.count} observations · mean {((hist.sum / hist.count) * unitScale).toFixed(1)} {unit}</p>
    </div>
  );
}

export function CompareBars({ data, series, unit, label }: { data: Record<string, string | number | null>[]; series: SeriesDef[]; unit: string; label: string }) {
  if (!data.length) return <EmptyState title="Nothing to compare">Select at least one run.</EmptyState>;
  const config = Object.fromEntries(series.map((s) => [s.key, { label: s.label, color: s.color }])) as ChartConfig;
  return (
    <div role="img" aria-label={`${label}, ${data.length} runs compared`}>
      <ChartContainer config={config} className="w-full" style={{ height: 220 }}>
        <BarChart data={data} margin={{ left: 0, right: 8, top: 4 }}>
          <CartesianGrid vertical={false} strokeOpacity={0.15} />
          <XAxis dataKey="name" tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
          <YAxis width={44} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
          <ChartTooltip content={<ChartTooltipContent formatter={(v, n) => <span className="tabular">{config[n as string]?.label}: {v == null ? "N/A" : `${(v as number).toFixed(2)} ${unit}`}</span>} />} />
          {series.length > 1 ? <ChartLegend content={<ChartLegendContent />} /> : null}
          {series.map((s) => <Bar key={s.key} dataKey={s.key} fill={s.color} stroke="var(--card)" strokeWidth={2} radius={[4, 4, 0, 0]} isAnimationActive={false} />)}
        </BarChart>
      </ChartContainer>
    </div>
  );
}
