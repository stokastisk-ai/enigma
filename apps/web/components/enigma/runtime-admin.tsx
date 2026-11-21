"use client";
import { useState } from "react";
import { Copy, KeyRound, Plus, RefreshCw, ShieldOff } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, ApiFailure } from "@/lib/api";
import { usePoll } from "@/hooks/use-poll";
import type { Credential, Runtime, RuntimeToken } from "@/lib/types";
import { useConsole } from "./console-context";
import { ErrorState, Spinner } from "./status";

const when = (s: string | null, utc: boolean) => (s ? new Date(s).toLocaleString([], { timeZone: utc ? "UTC" : undefined }) : "—");

export function TokenReveal({ token, mock, onClose }: { token: RuntimeToken | null; mock: boolean; onClose: () => void }) {
  const [shown, setShown] = useState(false);
  const url = typeof location !== "undefined" ? location.origin : "https://YOUR-ENIGMA-DOMAIN";
  const snippet = `# Kaggle notebook: store the key as a Kaggle Secret named ENIGMA_RUNTIME_TOKEN, never paste it into a cell.\nimport os\nos.environ["ENIGMA_API_URL"] = "${url.replace(/\/$/, "")}"   # your Enigma API base URL\nos.environ["ENIGMA_MODE"] = "${mock ? "mock" : "stokastisk"}"`;
  return (
    <Dialog open={!!token} onOpenChange={(o) => { if (!o) { setShown(false); onClose(); } }}>
      <DialogContent data-testid="token-dialog">
        <DialogHeader><DialogTitle>Your runtime key</DialogTitle><DialogDescription>Copy it now. For your security only its hash is stored, so it can never be shown again. Treat it like a password.</DialogDescription></DialogHeader>
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-md border bg-muted px-2 py-1.5 font-mono text-xs" data-testid="token-value">{shown ? token?.token : `${token?.prefix ? `enig_rt_${token.prefix}_` : ""}${"•".repeat(24)}`}</code>
          <Button size="sm" variant="outline" onClick={() => setShown((s) => !s)}>{shown ? "Hide" : "Reveal"}</Button>
          <Button size="sm" onClick={async () => { await navigator.clipboard.writeText(token!.token).then(() => toast.success("Key copied"), () => toast.error("Copy failed: reveal and copy manually")); }} data-testid="copy-token"><Copy aria-hidden />Copy</Button>
        </div>
        <p className="text-xs text-muted-foreground">Expires {token ? new Date(token.expires_at).toLocaleDateString() : ""}. Scope: connect this one runtime only.</p>
        <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-xs">{snippet}</pre>
        <DialogFooter><Button onClick={() => { setShown(false); onClose(); }}>I have saved the key</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function NewRuntimeButton() {
  const c = useConsole();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [mock, setMock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiFailure | null>(null);
  const [token, setToken] = useState<{ t: RuntimeToken; mock: boolean } | null>(null);
  if (!c.canAdmin || !c.project) return null;
  async function create(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      const r = await api<{ runtime: Runtime; credential: RuntimeToken }>(`projects/${c.project!.id}/runtimes`, { method: "POST", body: { name, is_mock: mock, expires_days: 30 } });
      setOpen(false); setName(""); setToken({ t: r.credential, mock }); c.reloadRuntimes(); c.setRuntimeId(r.runtime.id);
    } catch (x) { setErr(x as ApiFailure); } finally { setBusy(false); }
  }
  return (
    <>
      <Button onClick={() => setOpen(true)} data-testid="new-runtime"><Plus aria-hidden />New runtime</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <form onSubmit={create} className="flex flex-col gap-4">
            <DialogHeader><DialogTitle>New runtime</DialogTitle><DialogDescription>A runtime is one GPU host (for example a Kaggle notebook). Creating it issues a key that can connect only this runtime.</DialogDescription></DialogHeader>
            <div className="flex flex-col gap-2"><Label htmlFor="rname">Name</Label><Input id="rname" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="kaggle-t4x2" autoFocus /></div>
            <div className="flex items-start gap-3"><Switch id="mock" checked={mock} onCheckedChange={setMock} /><div><Label htmlFor="mock">Mock connector (synthetic data)</Label><p className="text-xs text-muted-foreground">For trying the UI without a GPU. Everything it reports is permanently labelled synthetic. A mock key cannot be used for a real GPU, and vice versa.</p></div></div>
            {err ? <ErrorState error={err} title="Could not create the runtime" /> : null}
            <DialogFooter><Button type="submit" disabled={busy || !name.trim()} data-testid="create-runtime">{busy ? "Creating…" : "Create and show key"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <TokenReveal token={token?.t ?? null} mock={token?.mock ?? false} onClose={() => setToken(null)} />
    </>
  );
}

export function CredentialsPanel({ runtime }: { runtime: Runtime }) {
  const c = useConsole();
  const creds = usePoll<{ items: Credential[] }>(c.canAdmin ? (s) => api(`runtimes/${runtime.id}/credentials`, { signal: s }) : null, 0, [runtime.id]);
  const [token, setToken] = useState<RuntimeToken | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"rotate" | "revoke" | null>(null);
  async function act(kind: "rotate" | "revoke") {
    setBusy(true);
    try {
      if (kind === "rotate") { setToken(await api<RuntimeToken>(`runtimes/${runtime.id}/rotate`, { method: "POST" })); toast.success("New key issued. The old key no longer works."); }
      else { await api(`runtimes/${runtime.id}/revoke`, { method: "POST" }); toast.success("Key revoked. The connector will stop."); }
      creds.refresh();
    } catch (e) { toast.error((e as ApiFailure).message); } finally { setBusy(false); setConfirm(null); }
  }
  if (!c.canAdmin) return <p className="text-sm text-muted-foreground">Only workspace admins can manage keys.</p>;
  return (
    <div className="flex flex-col gap-3" data-testid={`creds-${runtime.id}`}>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={() => setConfirm("rotate")} data-testid="rotate-key"><RefreshCw aria-hidden />Rotate key</Button>
        <Button size="sm" variant="destructive" disabled={busy} onClick={() => setConfirm("revoke")} data-testid="revoke-key"><ShieldOff aria-hidden />Revoke all keys</Button>
      </div>
      {creds.loading ? <Spinner /> : creds.error ? <ErrorState error={creds.error} onRetry={creds.refresh} /> : (
        <Table><TableHeader><TableRow><TableHead>Key</TableHead><TableHead>Created</TableHead><TableHead>Expires</TableHead><TableHead>Last used</TableHead><TableHead>State</TableHead></TableRow></TableHeader>
          <TableBody>{creds.data?.items.map((k) => (
            <TableRow key={k.id}><TableCell className="font-mono text-xs">enig_rt_{k.prefix}_…</TableCell><TableCell className="tabular text-xs">{when(k.created_at, c.utc)}</TableCell><TableCell className="tabular text-xs">{when(k.expires_at, c.utc)}</TableCell><TableCell className="tabular text-xs">{when(k.last_used_at, c.utc)}</TableCell><TableCell>{k.revoked_at ? <span className="text-bad">Revoked</span> : new Date(k.expires_at) < new Date() ? <span className="text-warn">Expired</span> : <span className="text-ok">Active</span>}</TableCell></TableRow>
          ))}</TableBody></Table>
      )}
      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{confirm === "rotate" ? "Rotate the key?" : "Revoke all keys?"}</DialogTitle><DialogDescription>{confirm === "rotate" ? "A new key is issued and every older key stops working immediately. A connector using the old key will disconnect." : "Every key for this runtime stops working immediately. The connector will disconnect until you issue a new key."}</DialogDescription></DialogHeader>
          <DialogFooter><Button variant="outline" onClick={() => setConfirm(null)}>Cancel</Button><Button variant={confirm === "revoke" ? "destructive" : "default"} disabled={busy} onClick={() => confirm && act(confirm)} data-testid="confirm-key-action"><KeyRound aria-hidden />{confirm === "rotate" ? "Rotate" : "Revoke"}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <TokenReveal token={token} mock={runtime.is_mock} onClose={() => setToken(null)} />
    </div>
  );
}
