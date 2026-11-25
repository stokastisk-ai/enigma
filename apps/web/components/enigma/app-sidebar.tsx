"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Boxes, Cpu, Gauge, KeyRound, MessagesSquare, Server, Timer } from "lucide-react";
import { useTheme } from "next-themes";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem } from "@/components/ui/sidebar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { useConsole } from "./console-context";
import { RuntimeStatusBadge } from "./status";

type NavItem = { href: string; label: string; Icon: typeof Gauge; exact?: boolean };
const NAV: NavItem[] = [
  { href: "/app", label: "Overview", Icon: Gauge, exact: true },
  { href: "/app/gpus", label: "GPUs", Icon: Cpu },
  { href: "/app/inference", label: "vLLM inference", Icon: Activity },
  { href: "/app/runs", label: "Runs & benchmarks", Icon: Timer },
  { href: "/app/playground", label: "Playground", Icon: MessagesSquare },
];
const MANAGE: NavItem[] = [
  { href: "/app/runtimes", label: "Runtimes", Icon: Server },
  { href: "/app/projects", label: "Projects", Icon: Boxes },
  { href: "/app/settings/api-keys", label: "API keys", Icon: KeyRound },
];
export function AppSidebar({ authSlot }: { authSlot?: React.ReactNode }) {
  const path = usePathname();
  const c = useConsole();
  const { resolvedTheme, setTheme } = useTheme();
  const active = (href: string, exact?: boolean) => (exact ? path === href : path === href || path.startsWith(`${href}/`));
  const wsItems = (c.workspaces ?? []).map((w) => ({ value: w.id, label: w.name }));
  const projItems = c.projects.map((p) => ({ value: p.id, label: p.name }));
  const rtItems = c.runtimes.map((r) => ({ value: r.id, label: `${r.name}${r.is_mock ? " (mock)" : ""}` }));

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="gap-3">
        <Link href="/app" className="flex items-center gap-2 px-2 pt-1 font-semibold tracking-tight">
          <span aria-hidden className="grid size-6 place-items-center rounded-md bg-primary font-mono text-xs text-primary-foreground">E</span>
          <span className="group-data-[collapsible=icon]:hidden">Enigma</span>
        </Link>
        <div className="flex flex-col gap-2 px-1 group-data-[collapsible=icon]:hidden">
          <Select items={wsItems} value={c.workspace?.id ?? null} onValueChange={(v) => v && c.setWorkspaceId(v)}>
            <SelectTrigger size="sm" className="w-full" aria-label="Workspace" data-testid="workspace-picker"><SelectValue placeholder="Workspace" /></SelectTrigger>
            <SelectContent>{wsItems.map((i) => <SelectItem key={i.value} value={i.value}>{i.label}</SelectItem>)}</SelectContent>
          </Select>
          <Select items={projItems} value={c.project?.id ?? null} onValueChange={(v) => v && c.setProjectId(v)}>
            <SelectTrigger size="sm" className="w-full" aria-label="Project" data-testid="project-picker"><SelectValue placeholder="Project" /></SelectTrigger>
            <SelectContent>{projItems.map((i) => <SelectItem key={i.value} value={i.value}>{i.label}</SelectItem>)}</SelectContent>
          </Select>
          <Select items={rtItems} value={c.runtime?.id ?? null} onValueChange={(v) => c.setRuntimeId(v)}>
            <SelectTrigger size="sm" className="w-full" aria-label="Runtime" data-testid="runtime-picker"><SelectValue placeholder="No runtime" /></SelectTrigger>
            <SelectContent>{rtItems.map((i) => <SelectItem key={i.value} value={i.value}>{i.label}</SelectItem>)}</SelectContent>
          </Select>
          {c.runtime ? <div className="flex items-center justify-between px-1"><span className="text-xs text-muted-foreground">Runtime</span><RuntimeStatusBadge status={c.runtime.status} /></div> : null}
        </div>
      </SidebarHeader>
      <SidebarContent>
        {[{ label: "Observe", items: NAV }, { label: "Manage", items: MANAGE }].map((g) => (
          <SidebarGroup key={g.label}>
            <SidebarGroupLabel>{g.label}</SidebarGroupLabel>
            <SidebarMenu>
              {g.items.map(({ href, label, Icon, exact }) => (
                <SidebarMenuItem key={href}>
                  <SidebarMenuButton isActive={active(href, exact)} tooltip={label} render={<Link href={href} aria-current={active(href, exact) ? "page" : undefined} />}>
                    <Icon aria-hidden /><span>{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter className="gap-2 group-data-[collapsible=icon]:hidden">
        <div className="flex items-center justify-between px-2">
          <Label htmlFor="utc" className="text-xs text-muted-foreground">Timestamps in UTC</Label>
          <Switch id="utc" size="sm" checked={c.utc} onCheckedChange={c.setUtc} />
        </div>
        <Button variant="ghost" size="sm" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")} aria-label="Toggle colour theme">
          {resolvedTheme === "dark" ? "Light theme" : "Dark theme"}
        </Button>
        {authSlot}
      </SidebarFooter>
    </Sidebar>
  );
}
