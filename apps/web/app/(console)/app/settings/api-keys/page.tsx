"use client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CredentialsPanel, NewRuntimeButton } from "@/components/enigma/runtime-admin";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState } from "@/components/enigma/status";

export default function ApiKeys() {
  const c = useConsole();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2"><h1 className="text-lg font-semibold">API keys</h1><span className="ml-auto"><NewRuntimeButton /></span></div>
      <p className="max-w-2xl text-sm text-muted-foreground">Each runtime key lets one GPU host connect to Enigma, and nothing else. Keys are stored only as hashes. Rotate or revoke a key at any time; a revoked connector stops within seconds.</p>
      {c.runtimes.length === 0 ? <EmptyState title="No runtimes yet">Create a runtime to issue its first key.</EmptyState> : c.runtimes.map((r) => (
        <Card key={r.id} size="sm"><CardHeader><CardTitle>{r.name}{r.is_mock ? " (mock)" : ""}</CardTitle></CardHeader><CardContent><CredentialsPanel runtime={r} /></CardContent></Card>
      ))}
    </div>
  );
}
