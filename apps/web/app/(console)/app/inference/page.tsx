"use client";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartCard, HistogramBars, TimeSeries, vllmRows, S1, S2 } from "@/components/enigma/charts";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, ErrorState, Freshness, ProvenanceBadge, StaleBanner } from "@/components/enigma/status";
import { useVllmMetrics } from "@/components/enigma/use-metrics";

export default function Inference() {
  const c = useConsole();
  const m = useVllmMetrics();
  if (!c.runtime) return <EmptyState title="No runtime selected">Add a runtime to see vLLM metrics.</EmptyState>;
  if (m.error && !m.data) return <ErrorState error={m.error} onRetry={m.refresh} />;
  const pts = m.data?.points ?? [];
  const ms = (f: (p: (typeof pts)[number]) => number | null) => (p: (typeof pts)[number]) => { const v = f(p); return v == null ? null : v * 1000; };
  const lat = (a: "ttft" | "e2e" | "itl") => vllmRows(pts, { p50: ms((p) => p[`${a}_p50_s`]), p95: ms((p) => p[`${a}_p95_s`]) });
  const latSeries = [{ key: "p50", label: "p50", color: S1 }, { key: "p95", label: "p95", color: S2, dashed: true }];
  const cum = m.data?.latest_cumulative;
  const resets = pts.filter((p) => p.counter_reset).length;
  return (
    <div className="flex flex-col gap-4">
      <StaleBanner updatedAt={m.updatedAt} error={m.error} />
      <div className="flex flex-wrap items-center gap-2"><h1 className="text-lg font-semibold">vLLM inference</h1><ProvenanceBadge p={m.data?.provenance} /><Freshness ageS={m.data?.latest_age_s} />
        {resets ? <span className="text-xs text-warn" data-testid="reset-note">{resets} server restart{resets > 1 ? "s" : ""} in this range: rates restart at each one.</span> : null}</div>
      <p className="text-xs text-muted-foreground">Latency percentiles are computed from the difference between consecutive cumulative histogram scrapes (Prometheus <code>histogram_quantile</code> interpolation), so they describe each interval, not the whole server lifetime.</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Requests in flight" testId="chart-queue"><TimeSeries label="Requests" rows={vllmRows(pts, { running: (p) => p.num_requests_running, waiting: (p) => p.num_requests_waiting })} series={[{ key: "running", label: "Running", color: S1 }, { key: "waiting", label: "Waiting", color: S2 }]} unit="req" utc={c.utc} format={(v) => v.toFixed(0)} /></ChartCard>
        <ChartCard title="KV-cache usage" description="Percent of cache blocks in use"><TimeSeries label="KV cache" rows={vllmRows(pts, { kv: (p) => (p.kv_cache_usage_perc == null ? null : p.kv_cache_usage_perc * 100) })} series={[{ key: "kv", label: "KV cache", color: S1 }]} unit="%" utc={c.utc} domain={[0, 100]} area /></ChartCard>
        <ChartCard title="Token throughput" description="Rate of vLLM's cumulative token counters" testId="chart-throughput"><TimeSeries label="Tokens per second" rows={vllmRows(pts, { gen: (p) => p.generation_tokens_per_s, prompt: (p) => p.prompt_tokens_per_s })} series={[{ key: "gen", label: "Generated", color: S1 }, { key: "prompt", label: "Prompt", color: S2 }]} unit="tok/s" utc={c.utc} area /></ChartCard>
        <ChartCard title="Time to first token" description="ms"><TimeSeries label="TTFT" rows={lat("ttft")} series={latSeries} unit="ms" utc={c.utc} /></ChartCard>
        <ChartCard title="End-to-end request latency" description="ms"><TimeSeries label="End-to-end latency" rows={lat("e2e")} series={latSeries} unit="ms" utc={c.utc} /></ChartCard>
        <ChartCard title="Inter-token latency" description="ms"><TimeSeries label="Inter-token latency" rows={lat("itl")} series={latSeries} unit="ms" utc={c.utc} /></ChartCard>
        <ChartCard title="TTFT distribution"><HistogramBars hist={cum?.ttft} label="Time to first token" /></ChartCard>
        <ChartCard title="End-to-end latency distribution"><HistogramBars hist={cum?.e2e} label="End-to-end latency" /></ChartCard>
      </div>
      <Card><CardHeader><CardTitle>Finished requests by reason</CardTitle></CardHeader><CardContent>
        {cum?.request_success ? <Table><TableHeader><TableRow><TableHead>Reason</TableHead><TableHead>Count (cumulative)</TableHead></TableRow></TableHeader><TableBody>{Object.entries(cum.request_success).map(([k, v]) => <TableRow key={k}><TableCell>{k}</TableCell><TableCell className="tabular">{v}</TableCell></TableRow>)}</TableBody></Table> : <p className="text-sm text-muted-foreground">Not reported.</p>}
        <p className="mt-2 text-xs text-muted-foreground">Preemptions in range: {pts.some((p) => p.preemptions_in_interval != null) ? pts.reduce((a, p) => a + (p.preemptions_in_interval ?? 0), 0) : "N/A"}</p>
      </CardContent></Card>
    </div>
  );
}
