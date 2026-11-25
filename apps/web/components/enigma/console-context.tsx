"use client";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiFailure } from "@/lib/api";
import { usePoll } from "@/hooks/use-poll";
import { RUNTIME_POLL_LIVE_MS, runtimeListPollMs } from "@/lib/session";
import type { Project, Runtime, Workspace } from "@/lib/types";

export const RANGES = ["5m", "15m", "1h", "6h", "24h", "7d"] as const;
export type Range = (typeof RANGES)[number];

type Ctx = {
  workspaces: Workspace[] | null; workspace: Workspace | null; setWorkspaceId: (id: string) => void;
  projects: Project[]; project: Project | null; setProjectId: (id: string) => void;
  runtimes: Runtime[]; runtime: Runtime | null; setRuntimeId: (id: string | null) => void;
  runtimesError: ApiFailure | null; runtimesUpdatedAt: number | null; reloadRuntimes: () => void; reloadProjects: () => void;
  range: Range; setRange: (r: Range) => void; utc: boolean; setUtc: (u: boolean) => void; loading: boolean; error: ApiFailure | null;
  canAdmin: boolean; canWrite: boolean;
  /** Enigma platform administrator (ENIGMA_ADMIN_SUBJECTS). Informational only; the BACKEND is the real gate. */
  isAdmin: boolean;
  /** Public origin of the FastAPI service for GPU-host connectors (server-provided at runtime; "" when not configured). */
  publicApiUrl: string;
};
const C = createContext<Ctx | null>(null);
export const useConsole = () => { const v = useContext(C); if (!v) throw new Error("useConsole outside provider"); return v; };

const store = {
  get: (k: string) => { try { return window.localStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string | null) => { try { if (v === null) window.localStorage.removeItem(k); else window.localStorage.setItem(k, v); } catch { /* storage unavailable: selections just won't persist */ } },
};

export function ConsoleProvider({ children, publicApiUrl }: { children: React.ReactNode; publicApiUrl: string }) {
  const [wsId, setWsId] = useState<string | null>(null);
  const [projId, setProjId] = useState<string | null>(null);
  const [rtId, setRtId] = useState<string | null>(null);
  const [range, setRangeState] = useState<Range>("15m");
  const [utc, setUtcState] = useState(false);
  const [booted, setBooted] = useState(false);

  // Restoring saved selections after mount is a sync with an external system (localStorage); doing it in render would break SSR hydration.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    setWsId(store.get("enigma.ws")); setProjId(store.get("enigma.project")); setRtId(store.get("enigma.runtime"));
    const r = store.get("enigma.range") as Range | null; if (r && (RANGES as readonly string[]).includes(r)) setRangeState(r);
    setUtcState(store.get("enigma.utc") === "1"); setBooted(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  const mePoll = usePoll<{ is_admin?: boolean }>((s) => api("me", { signal: s }), 0, []);
  const wsPoll = usePoll<{ items: Workspace[] }>((s) => api("workspaces", { signal: s }), 0, []);
  const workspaces = wsPoll.data?.items ?? null;
  const workspace = useMemo(() => workspaces?.find((w) => w.id === wsId) ?? workspaces?.[0] ?? null, [workspaces, wsId]);

  const projPoll = usePoll<{ items: Project[] }>(workspace ? (s) => api(`workspaces/${workspace.id}/projects`, { signal: s }) : null, 0, [workspace?.id]);
  const projects = useMemo(() => projPoll.data?.items ?? [], [projPoll.data]);
  const project = useMemo(() => projects.find((p) => p.id === projId) ?? projects[0] ?? null, [projects, projId]);

  const rtPoll = usePoll<{ items: Runtime[] }>(project ? (s) => api(`projects/${project.id}/runtimes`, { signal: s }) : null, RUNTIME_POLL_LIVE_MS, [project?.id], (d) => runtimeListPollMs((d?.items ?? []).map((r) => r.status)));
  const runtimes = useMemo(() => rtPoll.data?.items ?? [], [rtPoll.data]);
  const runtime = useMemo(() => runtimes.find((r) => r.id === rtId) ?? runtimes.find((r) => r.status === "online") ?? runtimes[0] ?? null, [runtimes, rtId]);

  const value: Ctx = {
    workspaces, workspace, setWorkspaceId: (id) => { setWsId(id); setProjId(null); setRtId(null); store.set("enigma.ws", id); store.set("enigma.project", null); store.set("enigma.runtime", null); },
    projects, project, setProjectId: (id) => { setProjId(id); setRtId(null); store.set("enigma.project", id); store.set("enigma.runtime", null); },
    runtimes, runtime, setRuntimeId: (id) => { setRtId(id); store.set("enigma.runtime", id); },
    runtimesError: rtPoll.error, runtimesUpdatedAt: rtPoll.updatedAt, reloadRuntimes: rtPoll.refresh, reloadProjects: projPoll.refresh,
    range, setRange: (r) => { setRangeState(r); store.set("enigma.range", r); },
    utc, setUtc: (u) => { setUtcState(u); store.set("enigma.utc", u ? "1" : "0"); },
    loading: !booted || wsPoll.loading || (!!workspace && projPoll.loading), error: wsPoll.error ?? projPoll.error,
    canAdmin: workspace?.role === "owner" || workspace?.role === "admin", canWrite: workspace?.role !== "viewer" && !!workspace,
    isAdmin: mePoll.data?.is_admin === true,
    publicApiUrl,
  };
  return <C.Provider value={value}>{children}</C.Provider>;
}
