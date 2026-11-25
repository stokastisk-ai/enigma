"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NewRuntimeButton } from "@/components/enigma/runtime-admin";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, Freshness, MockBadge, RuntimeStatusBadge } from "@/components/enigma/status";
import { softwareText } from "@/lib/software";

export default function Runtimes() {
  const c = useConsole();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2"><h1 className="text-lg font-semibold">Runtimes</h1><span className="ml-auto"><NewRuntimeButton /></span></div>
      {c.runtimes.length === 0 ? <EmptyState title="No runtimes">{c.canAdmin ? "Create a runtime to get a scoped key for your GPU notebook." : "Ask a workspace admin to create a runtime."}</EmptyState> : (
        <div className="grid gap-3 md:grid-cols-2" data-testid="runtime-list">
          {c.runtimes.map((r) => (
            <Card key={r.id} size="sm" data-testid={`runtime-${r.name}`}>
              <CardHeader className="flex-row flex-wrap items-center gap-2"><CardTitle>{r.name}</CardTitle><RuntimeStatusBadge status={r.status} />{r.is_mock ? <MockBadge /> : null}<span className="ml-auto"><Freshness ageS={r.heartbeat_age_s} /></span></CardHeader>
              <CardContent className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <span className="text-muted-foreground">Model</span><span className="break-all">{r.served_model ?? "not reported"}</span>
                <span className="text-muted-foreground">Parallelism</span><span>{r.tp ? `TP${r.tp} / PP${r.pp}` : "—"}{r.max_model_len ? ` · context ${r.max_model_len}` : ""}</span>
                <span className="text-muted-foreground">GPUs</span><span>{r.capabilities.gpus?.length ? r.capabilities.gpus.map((g) => `${g.index}: ${g.name ?? "?"}`).join("; ") : "none reported"}</span>
                <span className="text-muted-foreground">Software (self-reported)</span><span className="break-all">{softwareText(r.binary_identity_reported)}</span>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <p className="text-xs text-muted-foreground">Software versions are reported by the connector and are not verified by Enigma.</p>
    </div>
  );
}
