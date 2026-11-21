"use client";
import { useState } from "react";
import { Cloud, ExternalLink, Server } from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChartCard, S1, S2, S3, TimeSeries, type Row, type SeriesDef } from "@/components/enigma/charts";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, ErrorState, Spinner, fmtAge } from "@/components/enigma/status";
import { useAwsMetrics, useAwsStatus } from "@/components/enigma/use-aws";
import type { AwsGroup, AwsMetrics, AwsProvenance, AwsResource, AwsStatus, ResourceStatus } from "@/lib/aws-types";

const RANGES = ["1h", "6h", "24h"] as const;

/** A duration ("3h 12m"), not an age: fmtAge adds "ago". */
function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${sec % 60}s` : `${sec}s`;
}

const STATUS_STYLE: Record<ResourceStatus, string> = {
  DEPLOYED: "border-ok/40 text-ok",
  SYNTHETIC: "border-mock/60 text-mock",
  NOT_DEPLOYED: "text-muted-foreground",
  UNKNOWN: "border-warn/50 text-warn",
  NOT_AUTHORIZED: "border-warn/50 text-warn",
};
const STATUS_LABEL: Record<ResourceStatus, string> = { DEPLOYED: "Deployed", SYNTHETIC: "Synthetic", NOT_DEPLOYED: "Not deployed", UNKNOWN: "Unknown", NOT_AUTHORIZED: "Not authorised" };

function StatusBadge({ s }: { s: ResourceStatus }) {
  return <Badge variant="outline" className={STATUS_STYLE[s]} data-testid="resource-status" data-status={s}>{STATUS_LABEL[s]}</Badge>;
}

function Source({ p }: { p: AwsProvenance }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid="aws-provenance" data-source={p.source}>
      {p.synthetic ? <Badge variant="outline" className="border-mock/60 text-mock" data-testid="synthetic-badge">SYNTHETIC demo data: no AWS resource is being read</Badge>
        : p.source === "aws" ? <Badge variant="outline" className="border-ok/40 text-ok">Read from AWS CloudWatch / service APIs</Badge>
        : <Badge variant="outline" className="text-muted-foreground">No AWS source</Badge>}
      {p.stale ? <Badge variant="outline" className="border-warn/50 text-warn" data-testid="stale-badge">STALE: last refresh failed</Badge> : null}
      {p.region ? <span className="text-muted-foreground">Region {p.region}</span> : null}
      <span className="text-muted-foreground">Env {p.environment}</span>
      <span className="text-muted-foreground tabular">Verified {fmtAge(p.age_s)} (cached up to {p.ttl_s}s)</span>
    </div>
  );
}

function Topology({ s }: { s: AwsStatus }) {
  const node = (id: string, suffix = "") => {
    const n = s.topology.find((t) => t.id === id);
    if (!n) return null;
    return <span className={`rounded-md border px-2 py-1 ${n.aws ? "" : "border-dashed text-muted-foreground"}`} data-testid={`topo-${n.id}`}>{n.label}{n.aws ? "" : " (not AWS)"}{suffix}</span>;
  };
  const arrow = <span aria-hidden className="text-muted-foreground">→</span>;
  const path = ["browser", "cloudfront", "web", "api", "ddb"];
  return (
    <Card size="sm" data-testid="aws-topology">
      <CardHeader><CardTitle>Architecture</CardTitle><CardDescription>Static topology. AWS is the CPU control plane only; the GPU is the user-launched Kaggle session and is not an AWS resource.</CardDescription></CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <div><p className="mb-1 text-xs text-muted-foreground">Request path</p>
          <ol className="flex flex-wrap items-center gap-2" aria-label="Request path">{path.map((id, i) => <li key={id} className="flex items-center gap-2">{node(id)}{i < path.length - 1 ? arrow : null}</li>)}</ol></div>
        <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">Sign-in:</span>{node("web")}{arrow}{node("cognito")}</div>
        <div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">GPU (outbound HTTPS only):</span>{node("kaggle")}{arrow}{node("api")}</div>
      </CardContent>
    </Card>
  );
}

function Detail({ r }: { r: AwsResource }) {
  const d = r.detail;
  const parts = Object.entries(d).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`);
  return <span className="text-muted-foreground">{parts.length ? parts.join(" · ") : "—"}</span>;
}

function merge(group: AwsGroup | undefined, keys: Record<string, string>): Row[] {
  if (!group) return [];
  const by = new Map<number, Row>();
  for (const [k, label] of Object.entries(keys)) {
    for (const p of group.series[k] ?? []) {
      const row = by.get(p.ts) ?? ({ ts: p.ts } as Row);
      row[label] = p.value;
      by.set(p.ts, row);
    }
  }
  return [...by.values()].sort((a, b) => a.ts - b.ts);
}
const defs = (labels: string[], colors = [S1, S2, S3]): SeriesDef[] => labels.map((l, i) => ({ key: l, label: l, color: colors[i % colors.length] }));

function Charts({ m, utc }: { m: AwsMetrics; utc: boolean }) {
  const g = m.groups;
  const panels: { id: string; title: string; unit: string; group: string; keys: Record<string, string>; area?: boolean; note?: string }[] = [
    { id: "web-traffic", title: "Web Lambda: invocations and errors", unit: "count", group: "lambda_web", keys: { invocations: "Invocations", errors: "Errors", throttles: "Throttles" } },
    { id: "web-latency", title: "Web Lambda: duration", unit: "ms", group: "lambda_web", keys: { duration_avg_ms: "Average", duration_p95_ms: "p95" } },
    { id: "api-traffic", title: "API Lambda: invocations and errors", unit: "count", group: "lambda_api", keys: { invocations: "Invocations", errors: "Errors", throttles: "Throttles" } },
    { id: "api-latency", title: "API Lambda: duration", unit: "ms", group: "lambda_api", keys: { duration_avg_ms: "Average", duration_p95_ms: "p95" } },
    { id: "ddb-capacity", title: "DynamoDB: consumed capacity", unit: "units", group: "dynamodb", keys: { read_units: "Read units", write_units: "Write units" } },
    { id: "ddb-throttle", title: "DynamoDB: throttling and conditional-check failures", unit: "count", group: "dynamodb", keys: { read_throttles: "Read throttles", write_throttles: "Write throttles", conditional_check_failed: "Conditional check failed" }, note: "Conditional-check failures are normal: they are lost claim races, not errors." },
    { id: "cf-traffic", title: "CloudFront: requests", unit: "count", group: "cloudfront", keys: { requests: "Requests" } },
    { id: "cf-errors", title: "CloudFront: error rates", unit: "%", group: "cloudfront", keys: { error_4xx_pct: "4xx", error_5xx_pct: "5xx" } },
  ];
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="aws-charts">
      {panels.filter((p) => g[p.group]).map((p) => {
        const grp = g[p.group];
        return (
          <ChartCard key={p.id} title={p.title} description={`${p.unit} · ${Math.round(m.period_s / 60)} min buckets${p.note ? ` · ${p.note}` : ""}`} testId={`aws-chart-${p.id}`}>
            {grp.state === "NO_DATA"
              ? <EmptyState title="No data">CloudWatch has no datapoints for this metric in the range. AWS emits some metrics only after the first request, so this is not shown as zero.</EmptyState>
              : <TimeSeries label={p.title} rows={merge(grp, p.keys)} series={defs(Object.values(p.keys))} unit={p.unit} utc={utc} area={false} format={(v) => (v >= 100 ? v.toFixed(0) : v.toFixed(1))} />}
          </ChartCard>
        );
      })}
    </div>
  );
}

export default function AwsInfrastructure() {
  const c = useConsole();
  const [range, setRange] = useState<(typeof RANGES)[number]>("1h");
  const st = useAwsStatus();
  const mt = useAwsMetrics(range);

  if (st.error?.status === 403 || st.error?.status === 401) {
    return <EmptyState title="Not authorised">The AWS Infrastructure page is only available to the Enigma platform administrator.</EmptyState>;
  }
  if (st.error && !st.data) return <ErrorState error={st.error} onRetry={st.refresh} />;
  if (!st.data) return <Spinner label="Loading AWS status" />;
  const s = st.data;
  const deployed = s.summary.state !== "NOT_DEPLOYED";

  return (
    <div className="flex flex-col gap-4" data-testid="aws-page">
      <div className="flex flex-wrap items-center gap-2"><Cloud aria-hidden className="size-5" /><h1 className="text-lg font-semibold">AWS Infrastructure</h1><Badge variant="outline" data-testid="aws-state" data-state={s.summary.state}>{s.summary.state.replace("_", " ")}</Badge></div>
      <Source p={s.provenance} />
      <Alert data-testid="aws-boundary"><Server aria-hidden /><AlertTitle>AWS operations only</AlertTitle><AlertDescription>{s.boundary}</AlertDescription></Alert>
      {s.summary.state === "NOT_DEPLOYED" ? <EmptyState title="No Enigma AWS resources deployed">{s.summary.message} Nothing has been created in AWS for Enigma yet, so there are no graphs to show.</EmptyState> : null}
      {s.summary.state === "UNKNOWN" || s.summary.state === "NOT_AUTHORIZED" || s.summary.state === "PARTIAL"
        ? <Alert role="status" className="border-warn/40"><AlertTitle>{s.summary.state.replace("_", " ")}</AlertTitle><AlertDescription>{s.summary.message}</AlertDescription></Alert> : null}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card size="sm"><CardHeader><CardTitle>API host</CardTitle></CardHeader><CardContent className="text-sm" data-testid="aws-api-health">
          <p>Status: <span className="font-medium">{s.app.api.status}</span></p>
          <p className="text-muted-foreground">v{s.app.api.version ?? "?"} · store {s.app.api.store ?? "?"} · up {fmtDuration(s.app.api.uptime_s)}</p></CardContent></Card>
        <Card size="sm"><CardHeader><CardTitle>Build</CardTitle></CardHeader><CardContent className="text-sm" data-testid="aws-build"><span className="font-mono">{s.app.build_sha ?? "unknown"}</span><p className="text-muted-foreground">env {s.app.environment}</p></CardContent></Card>
        <Card size="sm"><CardHeader><CardTitle>Resources</CardTitle></CardHeader><CardContent className="text-sm tabular" data-testid="aws-resource-count">{s.provenance.synthetic
          ? `${s.resources.length} synthetic (demo)`
          : `${s.resources.filter((r) => r.status === "DEPLOYED").length} / ${s.resources.length} deployed`}</CardContent></Card>
        <Card size="sm" data-testid="aws-cost"><CardHeader><CardTitle>Cost and credits</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-1 text-sm">
            <p className="text-muted-foreground">Credit expiry: {s.cost.credits.expiry}. Enigma does not call billing APIs.</p>
            {[["Billing", s.cost.links.billing_home], ["Credits", s.cost.links.credits], ["Free Tier usage", s.cost.links.free_tier], ["Cost Explorer", s.cost.links.cost_explorer]].map(([l, u]) => (
              <a key={l} href={u} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 underline underline-offset-2">{l}<ExternalLink aria-hidden className="size-3" /><span className="sr-only"> (opens the AWS console)</span></a>
            ))}
          </CardContent></Card>
      </div>

      <Topology s={s} />

      <Card>
        <CardHeader><CardTitle>Resource inventory</CardTitle><CardDescription>Only resources named in the approved manifest. Read-only; ARNs and account ids are never shown.</CardDescription></CardHeader>
        <CardContent>
          {s.resources.length === 0 ? <p className="text-sm text-muted-foreground" data-testid="inventory-empty">No resources in the manifest or none configured.</p> : (
            <Table data-testid="aws-inventory">
              <TableHeader><TableRow><TableHead>Service</TableHead><TableHead>Resource</TableHead><TableHead>Status</TableHead><TableHead>Details</TableHead></TableRow></TableHeader>
              <TableBody>{s.resources.map((r) => (
                <TableRow key={r.key} data-testid={`resource-${r.key}`}><TableCell>{r.service}</TableCell><TableCell className="font-mono text-xs">{r.name}</TableCell><TableCell><StatusBadge s={r.status} /></TableCell><TableCell className="text-xs"><Detail r={r} /></TableCell></TableRow>
              ))}</TableBody>
            </Table>)}
        </CardContent>
      </Card>

      {deployed ? (
        <section className="flex flex-col gap-3" aria-label="AWS metrics">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">Operational metrics</h2>
            <Select items={RANGES.map((r) => ({ value: r, label: `Last ${r}` }))} value={range} onValueChange={(v) => v && setRange(v as (typeof RANGES)[number])}>
              <SelectTrigger size="sm" aria-label="AWS metrics range" data-testid="aws-range"><SelectValue /></SelectTrigger>
              <SelectContent>{RANGES.map((r) => <SelectItem key={r} value={r}>Last {r}</SelectItem>)}</SelectContent>
            </Select>
            {mt.data ? <Source p={mt.data.provenance} /> : null}
          </div>
          {mt.error && !mt.data ? <ErrorState error={mt.error} onRetry={mt.refresh} /> : null}
          {mt.data && mt.data.state && mt.data.state !== "OK" ? <Alert role="status" className="border-warn/40"><AlertTitle>{mt.data.state.replace("_", " ")}</AlertTitle><AlertDescription>Metrics could not be read.</AlertDescription></Alert> : null}
          {mt.data && Object.keys(mt.data.groups).length ? <Charts m={mt.data} utc={c.utc} /> : mt.data ? <EmptyState title="No metric groups">There is nothing to chart for the current manifest.</EmptyState> : <Spinner label="Loading metrics" />}
        </section>
      ) : null}
    </div>
  );
}
