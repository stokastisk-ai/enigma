"use client";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ChartCard, Sparkline, TimeSeries, VramBars, gpuSeries, mergeGpu, vllmRows, S2 } from "@/components/enigma/charts";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, ErrorState, Freshness, ProvenanceBadge, RuntimeStatusBadge, StaleBanner, fmt } from "@/components/enigma/status";
import { useGpuMetrics, useOverview } from "@/components/enigma/use-metrics";

function Stat({ label, value, sub, spark }: { label: string; value: React.ReactNode; sub?: React.ReactNode; spark?: React.ReactNode }) {
  return (
    <Card size="sm"><CardHeader><CardTitle className="text-xs font-normal text-muted-foreground">{label}</CardTitle></CardHeader>
      <CardContent className="flex flex-col gap-1"><div className="tabular text-2xl font-semibold" data-testid={`stat-${label.toLowerCase().replace(/\W+/g, "-")}`}>{value}</div>{sub ? <div className="text-xs text-muted-foreground">{sub}</div> : null}{spark}</CardContent></Card>
  );
}

export default function Overview() {
  const c = useConsole();
  const ov = useOverview();
  const gpu = useGpuMetrics();
  if (!c.runtimes.length && !c.loading) {
    return <EmptyState title="No runtime connected yet" action={<Button render={<Link href="/app/runtimes" />}>Add a runtime</Button>}>Create a runtime to get a scoped key, then start the connector in your Kaggle notebook (or use the labelled mock connector to try the UI).</EmptyState>;
  }
  if (ov.error && !ov.data) return <ErrorState error={ov.error} onRetry={ov.refresh} />;
  if (!ov.data) return <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-28" />)}</div>;

  const rt = c.runtime;
  const pts = ov.data.vllm_points;
  const last = pts[pts.length - 1];
  const rows = vllmRows(pts, { gen: (p) => p.generation_tokens_per_s, ttft: (p) => (p.ttft_p50_s == null ? null : p.ttft_p50_s * 1000), kv: (p) => (p.kv_cache_usage_perc == null ? null : p.kv_cache_usage_perc * 100), run: (p) => p.num_requests_running });
  const gpus = ov.data.gpus.filter((g) => !rt || g.runtime_id === rt.id);
  return (
    <div className="flex flex-col gap-4">
      <StaleBanner updatedAt={ov.updatedAt} error={ov.error} />
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">Overview</h1>
        {rt ? <><RuntimeStatusBadge status={rt.status} /><span className="text-sm text-muted-foreground">{rt.name} · {rt.served_model ?? "no model reported"}{rt.tp ? ` · TP${rt.tp}/PP${rt.pp}` : ""}{rt.max_model_len ? ` · ctx ${rt.max_model_len}` : ""}</span></> : null}
        <span className="ml-auto flex items-center gap-2"><ProvenanceBadge p={ov.data.provenance} /></span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Requests running" value={last?.num_requests_running ?? "N/A"} sub={`waiting: ${last?.num_requests_waiting ?? "N/A"}`} spark={<Sparkline rows={rows} k="run" label="Requests running" />} />
        <Stat label="Generation tokens/s" value={fmt.num(last?.generation_tokens_per_s)} sub="from vLLM counter deltas" spark={<Sparkline rows={rows} k="gen" label="Generation tokens per second" color={S2} />} />
        <Stat label="TTFT p50" value={fmt.s2ms(last?.ttft_p50_s)} sub="per-interval histogram delta" spark={<Sparkline rows={rows} k="ttft" label="TTFT p50" />} />
        <Stat label="KV cache" value={fmt.pct(last?.kv_cache_usage_perc == null ? null : last.kv_cache_usage_perc * 100)} sub="share of cache blocks in use" spark={<Sparkline rows={rows} k="kv" label="KV cache usage" color={S2} />} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="GPU utilisation" description="Measured per device" testId="chart-gpu-util" right={<ProvenanceBadge p={gpu.data?.provenance} />}>
          {gpu.error && !gpu.data ? <ErrorState error={gpu.error} onRetry={gpu.refresh} /> : <TimeSeries label="GPU utilisation" rows={mergeGpu(gpu.data?.series, "util_gpu_pct")} series={gpuSeries(gpu.data?.series)} unit="%" utc={c.utc} domain={[0, 100]} area />}
        </ChartCard>
        <ChartCard title="VRAM (latest sample)" description="Used vs free per GPU" right={<Freshness ageS={gpus[0]?.age_s} />}>
          <VramBars gpus={gpus.map((g) => ({ name: `GPU ${g.gpu_index}`, used: g.mem_used_mib, total: g.mem_total_mib }))} />
        </ChartCard>
      </div>
      <p className="text-xs text-muted-foreground">Colours: <span className="text-series-1">GPU 0</span> · <span className="text-series-2">GPU 1</span>. N/A means the sensor or metric was not reported, which is different from zero.</p>
    </div>
  );
}
