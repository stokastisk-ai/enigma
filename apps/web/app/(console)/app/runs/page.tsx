"use client";
import { useMemo, useState } from "react";
import { Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartCard, CompareBars, S1, S2 } from "@/components/enigma/charts";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, ErrorState, MockBadge, ProvenanceBadge, Spinner, fmt } from "@/components/enigma/status";
import { api } from "@/lib/api";
import { usePoll } from "@/hooks/use-poll";
import type { Run, RunSummary } from "@/lib/types";

type SortKey = "started_at" | "model" | "status";

export default function Runs() {
  const c = useConsole();
  const runs = usePoll<{ items: Run[] }>(c.project ? (s) => api(`projects/${c.project!.id}/runs`, { signal: s }) : null, 10000, [c.project?.id]);
  const [sel, setSel] = useState<string[]>([]);
  const [sort, setSort] = useState<{ k: SortKey; dir: 1 | -1 }>({ k: "started_at", dir: -1 });
  const [filter, setFilter] = useState<"all" | "active" | "mock" | "measured">("all");
  const rows = useMemo(() => {
    let r = runs.data?.items ?? [];
    if (filter === "active") r = r.filter((x) => x.status === "active");
    if (filter === "mock") r = r.filter((x) => x.is_mock);
    if (filter === "measured") r = r.filter((x) => !x.is_mock);
    return [...r].sort((a, b) => ((a[sort.k] ?? "") > (b[sort.k] ?? "") ? 1 : -1) * sort.dir);
  }, [runs.data, sort, filter]);
  const summaries = usePoll<RunSummary[]>(sel.length ? (s) => Promise.all(sel.map((id) => api<RunSummary>(`runs/${id}`, { signal: s }))) : null, 0, [sel.join(",")]);
  const label = (r: Run) => `${(r.model ?? "?").split("/").pop()} TP${r.tp ?? "?"}/PP${r.pp ?? "?"}${r.is_mock ? " (mock)" : ""} · ${new Date(r.started_at).toLocaleTimeString([], { hour12: false, timeZone: c.utc ? "UTC" : undefined })}`;
  const cmp = (summaries.data ?? []).map((s, i) => ({ name: `Run ${i + 1}`, tps: s.summary.generation_tokens_per_s_avg, ttft50: s.summary.ttft_p50_s == null ? null : s.summary.ttft_p50_s * 1000, ttft95: s.summary.ttft_p95_s == null ? null : s.summary.ttft_p95_s * 1000, util: s.summary.gpus["0"]?.util_gpu_avg_pct ?? null }));
  const toggle = (id: string) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : s.length >= 4 ? s : [...s, id]));
  const head = (k: SortKey, t: string) => <TableHead><button className="font-medium" onClick={() => setSort((s) => ({ k, dir: s.k === k ? (-s.dir as 1 | -1) : 1 }))} aria-label={`Sort by ${t}`}>{t}{sort.k === k ? (sort.dir === 1 ? " ▲" : " ▼") : ""}</button></TableHead>;
  const exportJson = () => { const blob = new Blob([JSON.stringify(summaries.data ?? [], null, 2)], { type: "application/json" }); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "enigma-run-comparison.json"; a.click(); URL.revokeObjectURL(a.href); };

  if (runs.error && !runs.data) return <ErrorState error={runs.error} onRetry={runs.refresh} />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2"><h1 className="text-lg font-semibold">Runs & benchmarks</h1>
        <span className="ml-auto flex gap-1" role="group" aria-label="Filter runs">{(["all", "active", "measured", "mock"] as const).map((f) => <Button key={f} size="xs" variant={filter === f ? "default" : "outline"} onClick={() => setFilter(f)} aria-pressed={filter === f}>{f}</Button>)}</span></div>
      <p className="text-xs text-muted-foreground">A run is one runtime session. Pick up to four to compare. Aggregates use only data the runtime reported; TP2/PP2 does not imply higher throughput than TP1 for models that fit one GPU.</p>
      {runs.loading ? <Spinner /> : rows.length === 0 ? <EmptyState title="No runs yet">A run is created each time a connector registers.</EmptyState> : (
        <Table data-testid="runs-table"><TableHeader><TableRow><TableHead className="w-8"><span className="sr-only">Compare</span></TableHead>{head("started_at", "Started")}{head("model", "Model")}<TableHead>TP / PP</TableHead>{head("status", "Status")}<TableHead>Source</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map((r) => (
            <TableRow key={r.id}><TableCell><input type="checkbox" aria-label={`Compare run started ${r.started_at}`} checked={sel.includes(r.id)} onChange={() => toggle(r.id)} disabled={!sel.includes(r.id) && sel.length >= 4} /></TableCell>
              <TableCell className="tabular text-xs">{new Date(r.started_at).toLocaleString([], { timeZone: c.utc ? "UTC" : undefined })}</TableCell><TableCell className="break-all text-xs">{r.model ?? "—"}</TableCell><TableCell className="tabular text-xs">{r.tp ?? "?"} / {r.pp ?? "?"}</TableCell><TableCell className="text-xs">{r.status}</TableCell><TableCell>{r.is_mock ? <MockBadge /> : <span className="text-xs text-ok">Measured</span>}</TableCell></TableRow>
          ))}</TableBody></Table>
      )}
      {sel.length ? (
        <div className="flex flex-col gap-4" data-testid="comparison">
          <div className="flex items-center gap-2"><h2 className="font-medium">Comparison</h2>{summaries.data ? <Button size="xs" variant="outline" onClick={exportJson}><Download aria-hidden />Export JSON</Button> : null}</div>
          {summaries.loading ? <Spinner /> : summaries.error ? <ErrorState error={summaries.error} onRetry={summaries.refresh} /> : (
            <>
              <div className="grid gap-4 lg:grid-cols-2">
                <ChartCard title="Generation throughput (avg)" description="tokens/s from vLLM counters"><CompareBars label="Generation throughput" data={cmp} series={[{ key: "tps", label: "tokens/s", color: S1 }]} unit="tok/s" /></ChartCard>
                <ChartCard title="Time to first token" description="ms, over the whole run"><CompareBars label="TTFT" data={cmp} series={[{ key: "ttft50", label: "p50", color: S1 }, { key: "ttft95", label: "p95", color: S2 }]} unit="ms" /></ChartCard>
              </div>
              <Table><TableHeader><TableRow><TableHead>Run</TableHead><TableHead>Config</TableHead><TableHead>Gen tokens</TableHead><TableHead>tok/s</TableHead><TableHead>TTFT p50 / p95</TableHead><TableHead>KV max</TableHead><TableHead>GPU0 util avg</TableHead><TableHead>Source</TableHead></TableRow></TableHeader>
                <TableBody>{(summaries.data ?? []).map((s, i) => (
                  <TableRow key={s.run.id}><TableCell>Run {i + 1}</TableCell><TableCell className="text-xs">{label(s.run)}</TableCell><TableCell className="tabular">{fmt.num(s.summary.generation_tokens, 0)}</TableCell><TableCell className="tabular">{fmt.num(s.summary.generation_tokens_per_s_avg)}</TableCell>
                    <TableCell className="tabular">{fmt.s2ms(s.summary.ttft_p50_s)} / {fmt.s2ms(s.summary.ttft_p95_s)}</TableCell><TableCell className="tabular">{fmt.pct(s.summary.kv_cache_max_perc)}</TableCell><TableCell className="tabular">{fmt.pct(s.summary.gpus["0"]?.util_gpu_avg_pct, 1)}</TableCell><TableCell><ProvenanceBadge p={s.provenance} /></TableCell></TableRow>
                ))}</TableBody></Table>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
