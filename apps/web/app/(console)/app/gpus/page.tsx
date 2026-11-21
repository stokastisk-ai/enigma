"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartCard, TimeSeries, VramBars, gpuSeries, mergeGpu } from "@/components/enigma/charts";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, ErrorState, Freshness, ProvenanceBadge, StaleBanner, fmt } from "@/components/enigma/status";
import { useGpuMetrics } from "@/components/enigma/use-metrics";
import type { GpuPoint } from "@/lib/types";

const PANELS: { field: keyof GpuPoint; title: string; unit: string; domain?: [number, number]; area?: boolean; conv?: (v: number) => number }[] = [
  { field: "util_gpu_pct", title: "Utilisation", unit: "%", domain: [0, 100], area: true },
  { field: "mem_used_mib", title: "VRAM used", unit: "GiB" },
  { field: "temp_c", title: "Temperature", unit: "°C" },
  { field: "power_w", title: "Power draw", unit: "W" },
  { field: "sm_clock_mhz", title: "SM clock", unit: "MHz" },
  { field: "pcie_rx_kib_s", title: "PCIe receive", unit: "KiB/s" },
];

export default function Gpus() {
  const c = useConsole();
  const g = useGpuMetrics();
  if (!c.runtime) return <EmptyState title="No runtime selected">Add a runtime to see GPU telemetry.</EmptyState>;
  if (g.error && !g.data) return <ErrorState error={g.error} onRetry={g.refresh} />;
  const latest = Object.entries(g.data?.latest ?? {}).sort(([a], [b]) => Number(a) - Number(b));
  const inv = c.runtime.capabilities.gpus ?? [];
  const series = g.data?.series;
  const scaled = (field: keyof GpuPoint, conv?: (v: number) => number) =>
    mergeGpu(series, field).map((r) => (conv ? Object.fromEntries(Object.entries(r).map(([k, v]) => [k, k === "ts" || v == null ? v : conv(v as number)])) as typeof r : r));
  return (
    <div className="flex flex-col gap-4">
      <StaleBanner updatedAt={g.updatedAt} error={g.error} />
      <div className="flex flex-wrap items-center gap-2"><h1 className="text-lg font-semibold">GPUs</h1><ProvenanceBadge p={g.data?.provenance} />{g.data?.truncated ? <span className="text-xs text-warn">Showing the first 20,000 samples of this range.</span> : null}</div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="gpu-cards">
        {latest.length === 0 ? <div className="sm:col-span-full"><EmptyState title="No GPU samples in this range">The connector has not uploaded GPU telemetry yet, or the range is older than the last upload.</EmptyState></div> : null}
        {latest.map(([idx, l]) => (
          <Card key={idx} size="sm" data-testid={`gpu-card-${idx}`}>
            <CardHeader className="flex-row items-center justify-between"><CardTitle>GPU {idx}</CardTitle><Freshness ageS={l.age_s} /></CardHeader>
            <CardContent className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm tabular">
              <span className="text-muted-foreground">Utilisation</span><span>{fmt.pct(l.util_gpu_pct)}</span>
              <span className="text-muted-foreground">VRAM</span><span>{fmt.mib(l.mem_used_mib)} / {fmt.mib(l.mem_total_mib)}</span>
              <span className="text-muted-foreground">Temperature</span><span>{l.temp_c == null ? "N/A" : `${l.temp_c.toFixed(0)} °C`}</span>
              <span className="text-muted-foreground">Power</span><span>{l.power_w == null ? "N/A" : `${l.power_w.toFixed(0)} W`}{l.power_limit_w != null ? ` / ${l.power_limit_w.toFixed(0)} W` : ""}</span>
              <span className="text-muted-foreground">PCIe rx / tx</span><span>{l.pcie_rx_kib_s == null ? "N/A" : `${l.pcie_rx_kib_s.toFixed(0)} / ${l.pcie_tx_kib_s == null ? "N/A" : l.pcie_tx_kib_s.toFixed(0)} KiB/s`}</span>
            </CardContent>
          </Card>
        ))}
      </div>
      {latest.length ? <ChartCard title="VRAM capacity (latest sample)" description="Used vs free"><VramBars gpus={latest.map(([i, l]) => ({ name: `GPU ${i}`, used: l.mem_used_mib, total: l.mem_total_mib }))} /></ChartCard> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        {PANELS.map((p) => (
          <ChartCard key={p.field} title={p.title} description={p.unit === "GiB" ? "Gibibytes" : undefined} testId={`chart-${p.field}`}>
            <TimeSeries label={p.title} rows={p.field === "mem_used_mib" ? scaled(p.field, (v) => v / 1024) : scaled(p.field)} series={gpuSeries(series)} unit={p.unit} utc={c.utc} domain={p.domain} area={p.area}
              empty={p.field.startsWith("pcie") || p.field === "power_w" ? "Not reported by this GPU or driver (N/A), so there is nothing to plot." : undefined} />
          </ChartCard>
        ))}
      </div>
      <Card>
        <CardHeader><CardTitle>Device inventory</CardTitle></CardHeader>
        <CardContent>
          {inv.length === 0 ? <p className="text-sm text-muted-foreground">The runtime has not reported a device inventory.</p> : (
            <Table><TableHeader><TableRow><TableHead>Index</TableHead><TableHead>Name</TableHead><TableHead>UUID</TableHead><TableHead>VRAM</TableHead></TableRow></TableHeader>
              <TableBody>{inv.map((d) => <TableRow key={d.index}><TableCell>{d.index}</TableCell><TableCell>{d.name ?? "N/A"}</TableCell><TableCell className="font-mono text-xs">{d.uuid ?? "N/A"}</TableCell><TableCell>{fmt.mib(d.memory_total_mib)}</TableCell></TableRow>)}</TableBody></Table>
          )}
          <p className="mt-2 text-xs text-muted-foreground">Sampled with NVML (nvidia-ml-py). Counters a device does not support are shown as N/A.</p>
        </CardContent>
      </Card>
    </div>
  );
}
