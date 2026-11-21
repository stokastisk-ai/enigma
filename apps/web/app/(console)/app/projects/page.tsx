"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ErrorState } from "@/components/enigma/status";
import { useConsole } from "@/components/enigma/console-context";
import { api, ApiFailure } from "@/lib/api";

export default function Projects() {
  const c = useConsole();
  const [name, setName] = useState("");
  const [err, setErr] = useState<ApiFailure | null>(null);
  async function create(e: React.FormEvent) {
    e.preventDefault(); setErr(null);
    try { await api(`workspaces/${c.workspace!.id}/projects`, { method: "POST", body: { name } }); setName(""); c.reloadProjects(); } catch (x) { setErr(x as ApiFailure); }
  }
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      <h1 className="text-lg font-semibold">Projects</h1>
      <Card size="sm"><CardHeader><CardTitle>{c.workspace?.name} ({c.workspace?.role})</CardTitle></CardHeader>
        <CardContent><ul className="flex flex-col gap-1 text-sm" data-testid="project-list">{c.projects.map((p) => <li key={p.id} className={p.id === c.project?.id ? "font-medium" : ""}>{p.name}{p.id === c.project?.id ? " · selected" : ""}</li>)}</ul></CardContent></Card>
      {c.canAdmin ? (
        <form onSubmit={create} className="flex items-end gap-2">
          <div className="flex flex-1 flex-col gap-1.5"><Label htmlFor="pn">New project</Label><Input id="pn" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Project name" /></div>
          <Button type="submit" disabled={!name.trim()}>Create</Button>
        </form>
      ) : <p className="text-xs text-muted-foreground">Only admins can create projects.</p>}
      {err ? <ErrorState error={err} title="Could not create the project" /> : null}
    </div>
  );
}
