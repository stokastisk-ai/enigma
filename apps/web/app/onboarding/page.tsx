"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState } from "@/components/enigma/status";
import { api, ApiFailure } from "@/lib/api";

export default function Onboarding() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiFailure | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { await api("workspaces", { method: "POST", body: { name } }); router.replace("/app"); }
    catch (x) { setErr(x as ApiFailure); setBusy(false); }
  }
  return (
    <main className="grid flex-1 place-items-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader><CardTitle>Create your workspace</CardTitle><CardDescription>A workspace holds your projects, GPU runtimes and conversations. Only members can see its data.</CardDescription></CardHeader>
        <CardContent>
          <form onSubmit={submit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2"><Label htmlFor="ws">Workspace name</Label><Input id="ws" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. My GPU lab" autoFocus /></div>
            {err ? <ErrorState error={err} title="Could not create the workspace" /> : null}
            <Button type="submit" disabled={busy || !name.trim()}>{busy ? "Creating…" : "Create workspace"}</Button>
          </form>
        </CardContent>
      </Card>
    </main>
  );
}
