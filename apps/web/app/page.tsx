import Link from "next/link";
import { Activity, Cpu, MessagesSquare, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { clerkConfigured, devAuthAllowed } from "@/lib/server/env";

const FEATURES = [
  { Icon: MessagesSquare, title: "Real streaming chat", body: "Prompts run on your own GPU through the Stokastisk-bundled vLLM. Every reply is model-generated, with measured time-to-first-token and latency." },
  { Icon: Cpu, title: "Per-GPU telemetry", body: "Utilisation, VRAM, temperature and power per device via NVML. Sensors a GPU does not expose show N/A, never a made-up zero." },
  { Icon: Activity, title: "vLLM internals", body: "Queue depth, KV-cache use and latency histograms from vLLM's own /metrics, with counter resets handled." },
  { Icon: ShieldCheck, title: "Private by default", body: "Clerk sign-in, per-workspace isolation, hashed scoped runtime keys. The GPU host only makes outbound HTTPS calls." },
];

export default function Landing() {
  const ready = clerkConfigured() || devAuthAllowed();
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-12 px-5 py-14">
      <header className="flex items-center justify-between">
        <span className="flex items-center gap-2 font-semibold tracking-tight"><span aria-hidden className="grid size-7 place-items-center rounded-md bg-primary font-mono text-sm text-primary-foreground">E</span>Enigma</span>
        <Button render={<Link href={clerkConfigured() ? "/sign-in" : "/app"} />} size="sm">{clerkConfigured() ? "Sign in" : "Open console"}</Button>
      </header>
      <section className="flex flex-col gap-5">
        <h1 className="max-w-3xl text-4xl font-semibold tracking-tight sm:text-5xl">Talk to your GPU. See what it is actually doing.</h1>
        <p className="max-w-2xl text-lg text-muted-foreground">Enigma is an inference playground and observability console for self-hosted vLLM. Connect a Kaggle dual-T4 notebook (or another GPU host you are authorised to use) and watch measured metrics next to a live chat.</p>
        <div className="flex flex-wrap gap-3">
          <Button size="lg" render={<Link href={clerkConfigured() ? "/sign-in" : "/app"} />}>{clerkConfigured() ? "Sign in to continue" : "Open the console"}</Button>
        </div>
        {!ready ? <p className="rounded-lg border border-warn/40 p-3 text-sm text-warn" role="status">Authentication is not configured on this deployment, so the console is closed. See docs/DEPLOYMENT.md.</p> : null}
      </section>
      <section className="grid gap-4 sm:grid-cols-2" aria-label="Features">
        {FEATURES.map(({ Icon, title, body }) => (
          <div key={title} className="rounded-xl border p-5"><Icon aria-hidden className="mb-3 size-5 text-series-1" /><h2 className="mb-1 font-medium">{title}</h2><p className="text-sm text-muted-foreground">{body}</p></div>
        ))}
      </section>
      <footer className="text-xs text-muted-foreground">Kaggle GPUs are for authorised personal experimentation and are not an always-on inference service. Charts only show data a runtime actually reported; synthetic demo data is always labelled.</footer>
    </div>
  );
}
