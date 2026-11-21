"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleSlash, FlaskConical, Loader2, WifiOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Provenance, RuntimeStatus } from "@/lib/types";
import type { ApiFailure } from "@/lib/api";

const RT: Record<RuntimeStatus, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  online: { label: "Online", cls: "border-ok/40 text-ok", Icon: CheckCircle2 },
  stale: { label: "Stale", cls: "border-warn/50 text-warn", Icon: AlertTriangle },
  offline: { label: "Offline", cls: "border-bad/50 text-bad", Icon: WifiOff },
  never_connected: { label: "Never connected", cls: "border-border text-muted-foreground", Icon: CircleSlash },
};

/** Status is always icon + text, never colour alone. */
export function RuntimeStatusBadge({ status, className }: { status: RuntimeStatus; className?: string }) {
  const { label, cls, Icon } = RT[status];
  return <Badge variant="outline" className={cn("gap-1 font-medium", cls, className)} data-testid="runtime-status" data-status={status}><Icon aria-hidden className="size-3" />{label}</Badge>;
}

/** Example data must never pass as a measurement. */
export function MockBadge({ className }: { className?: string }) {
  return <Badge variant="outline" className={cn("gap-1 border-mock/60 text-mock", className)} data-testid="mock-badge"><FlaskConical aria-hidden className="size-3" />Synthetic · not a GPU measurement</Badge>;
}

export function ProvenanceBadge({ p }: { p: Provenance | null | undefined }) {
  if (!p || p.rows === 0) return <Badge variant="outline" className="text-muted-foreground" data-testid="provenance">No data</Badge>;
  if (p.measured_rows === 0) return <span data-testid="provenance" data-kind="mock"><MockBadge /></span>;
  if (p.mock_rows === 0) return <Badge variant="outline" className="border-ok/40 text-ok" data-testid="provenance" data-kind="measured"><CheckCircle2 aria-hidden className="size-3" />Measured</Badge>;
  return <Badge variant="outline" className="border-warn/50 text-warn" data-testid="provenance" data-kind="mixed">Mixed: measured + synthetic</Badge>;
}

export function fmtAge(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  return `${(s / 3600).toFixed(1)}h ago`;
}

export function Freshness({ ageS, className }: { ageS: number | null | undefined; className?: string }) {
  const kind = ageS == null ? "none" : ageS <= 15 ? "live" : ageS <= 90 ? "stale" : "old";
  const text = kind === "none" ? "no samples" : kind === "live" ? `live · ${fmtAge(ageS)}` : `${kind} · ${fmtAge(ageS)}`;
  return <span className={cn("tabular text-xs", kind === "live" ? "text-ok" : kind === "stale" ? "text-warn" : "text-muted-foreground", className)} data-testid="freshness" data-kind={kind}>{text}</span>;
}

export function ErrorState({ error, onRetry, title = "Could not load data" }: { error: ApiFailure; onRetry?: () => void; title?: string }) {
  return (
    <Alert variant="destructive" role="alert" data-testid="error-state">
      <AlertTriangle aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-wrap items-center gap-2">
        <span>{error.message}{error.correlationId ? <span className="tabular text-xs opacity-70"> · ref {error.correlationId.slice(0, 8)}</span> : null}</span>
        {onRetry ? <Button size="xs" variant="outline" onClick={onRetry}>Retry</Button> : null}
      </AlertDescription>
    </Alert>
  );
}

export function StaleBanner({ updatedAt, error }: { updatedAt: number | null; error: ApiFailure | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!error) return; const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, [error]);
  if (!error || updatedAt == null) return null;
  const secs = Math.max(0, Math.round((now - updatedAt) / 1000));
  return (
    <Alert role="status" data-testid="stale-banner" className="border-warn/40">
      <WifiOff aria-hidden />
      <AlertTitle>Reconnecting…</AlertTitle>
      <AlertDescription>The server could not be reached. Showing the last data received {secs}s ago, which may be out of date.</AlertDescription>
    </Alert>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-10 text-center" data-testid="empty-state">
      <p className="font-medium">{title}</p>
      {children ? <div className="max-w-md text-sm text-muted-foreground">{children}</div> : null}
      {action}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <span role="status" className="inline-flex items-center gap-2 text-sm text-muted-foreground"><Loader2 aria-hidden className="size-4 animate-spin" />{label}</span>;
}

export const fmt = {
  pct: (v: number | null | undefined, d = 0) => (v == null ? "N/A" : `${v.toFixed(d)}%`),
  num: (v: number | null | undefined, d = 1) => (v == null ? "N/A" : v.toFixed(d)),
  mib: (v: number | null | undefined) => (v == null ? "N/A" : v >= 1024 ? `${(v / 1024).toFixed(1)} GiB` : `${v.toFixed(0)} MiB`),
  ms: (v: number | null | undefined) => (v == null ? "N/A" : v < 1000 ? `${v.toFixed(0)} ms` : `${(v / 1000).toFixed(2)} s`),
  s2ms: (v: number | null | undefined) => (v == null ? "N/A" : fmt.ms(v * 1000)),
};
