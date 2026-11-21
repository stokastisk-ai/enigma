"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { AppSidebar } from "./app-sidebar";
import { ConsoleProvider, RANGES, useConsole, type Range } from "./console-context";
import { ErrorState, Spinner, StaleBanner } from "./status";

function Header({ devAuth }: { devAuth: boolean }) {
  const c = useConsole();
  return (
    <header className="sticky top-0 z-10 flex h-12 items-center gap-2 border-b bg-background/80 px-3 backdrop-blur">
      <SidebarTrigger aria-label="Toggle navigation" />
      <Separator orientation="vertical" className="h-5" />
      <span className="truncate text-sm text-muted-foreground" data-testid="crumb">{c.workspace?.name ?? "…"} / {c.project?.name ?? "…"}</span>
      <div className="ml-auto flex items-center gap-2">
        {devAuth ? <span className="rounded-md border border-mock/60 px-2 py-0.5 text-xs text-mock" data-testid="dev-auth-banner" title="Local development login. Not available in production.">DEV AUTH · no Clerk</span> : null}
        <Select items={RANGES.map((r) => ({ value: r, label: `Last ${r}` }))} value={c.range} onValueChange={(v) => v && c.setRange(v as Range)}>
          <SelectTrigger size="sm" aria-label="Time range" data-testid="range-picker"><SelectValue /></SelectTrigger>
          <SelectContent>{RANGES.map((r) => <SelectItem key={r} value={r}>Last {r}</SelectItem>)}</SelectContent>
        </Select>
      </div>
    </header>
  );
}

function Gate({ children }: { children: React.ReactNode }) {
  const c = useConsole();
  const router = useRouter();
  const none = !c.loading && !c.error && c.workspaces !== null && c.workspaces.length === 0;
  useEffect(() => { if (none) router.replace("/onboarding"); }, [none, router]);
  if (c.error && !c.workspaces) return <div className="p-6"><ErrorState error={c.error} title="Could not load your workspace" onRetry={() => location.reload()} /></div>;
  if (c.loading || none) return <div className="p-6"><Spinner label="Loading workspace" /></div>;
  return <>{c.runtimesError ? <div className="px-4 pt-4"><StaleBanner updatedAt={c.runtimesUpdatedAt} error={c.runtimesError} /></div> : null}{children}</>;
}

export function ConsoleShell({ children, authSlot, devAuth }: { children: React.ReactNode; authSlot?: React.ReactNode; devAuth: boolean }) {
  return (
    <ConsoleProvider>
      <SidebarProvider>
        <AppSidebar authSlot={authSlot} />
        <SidebarInset>
          <Header devAuth={devAuth} />
          <main className="flex-1 p-4 md:p-6" id="main"><Gate>{children}</Gate></main>
        </SidebarInset>
      </SidebarProvider>
    </ConsoleProvider>
  );
}
