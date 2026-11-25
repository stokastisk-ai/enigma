"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageSquarePlus, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { MeasuredLine, MessageBubble, MessageScroller, PromptInput, RichText } from "@/components/enigma/chat-ui";
import { useConsole } from "@/components/enigma/console-context";
import { EmptyState, ErrorState, MockBadge, RuntimeStatusBadge, Spinner, fmt } from "@/components/enigma/status";
import { useChat } from "@/hooks/use-chat";
import { usePoll } from "@/hooks/use-poll";
import { api, ApiFailure } from "@/lib/api";
import type { Conversation, Measured } from "@/lib/types";

const FRIENDLY: Record<string, string> = {
  runtime_offline: "The runtime is offline, so the prompt was not sent. Start the connector in your GPU notebook and try again.",
  too_many_jobs: "The runtime is busy with other requests. Try again in a moment.",
  too_many_user_jobs: "You already have requests in progress. Wait for them to finish or stop one.",
  prompt_too_long: "That prompt is too long for the model's context window. Shorten it or start a new conversation.",
  max_tokens_too_large: "Max new tokens must be below half of the context window.",
  rate_limited: "You are sending messages too quickly.",
};

export default function Playground() {
  const c = useConsole();
  const convs = usePoll<{ items: Conversation[] }>(c.project ? (s) => api(`projects/${c.project!.id}/conversations`, { signal: s }) : null, 15000, [c.project?.id]);
  const [picked, setConvId] = useState<string | null>(null);
  const convId = picked ?? convs.data?.items[0]?.id ?? null;          // derived: the newest conversation unless the user picked one
  const [maxTokens, setMaxTokens] = useState(256);
  const [temperature, setTemperature] = useState(0.2);

  const chat = useChat(convId);

  const newChat = useCallback(async () => {
    if (!c.project) return;
    try {
      const conv = await api<Conversation>(`projects/${c.project.id}/conversations`, { method: "POST", body: { runtime_id: c.runtime?.id ?? null } });
      setConvId(conv.id); convs.refresh();
    } catch { /* surfaced on the next send; creating an empty conversation is non-critical */ }
  }, [c.project, c.runtime, convs]);

  const rt = c.runtime;
  const offline = !rt || rt.status === "offline" || rt.status === "never_connected";
  const reason = !rt ? "Add a runtime first." : rt.status === "never_connected" ? "This runtime has never connected." : "The runtime is offline.";
  const lastMeasured: Measured | undefined = useMemo(() => {
    if (chat.live?.result) return chat.live.result;
    return [...chat.messages].reverse().find((m) => m.role === "assistant" && m.meta.measured)?.meta.measured;
  }, [chat.live, chat.messages]);

  // A conversation always exists before the prompt box is usable, so `send` never has to create state mid-flight.
  const creating = useRef(false);
  useEffect(() => {
    if (convId || creating.current || !c.project || !c.canWrite || convs.loading || !convs.data || convs.data.items.length > 0) return;
    creating.current = true;
    void newChat().finally(() => { creating.current = false; });
  }, [convId, c.project, c.canWrite, convs.loading, convs.data, newChat]);

  async function onSend(content: string) {
    if (!rt || !convId) return false;
    return chat.send({ runtimeId: rt.id, content, maxTokens, temperature });
  }

  const err = chat.error;
  const live = chat.live;
  const liveActive = live && chat.running;
  return (
    <div className="grid h-[calc(100svh-6.5rem)] min-h-[28rem] gap-4 lg:grid-cols-[14rem_1fr_17rem]">
      <aside className="hidden min-h-0 flex-col gap-2 lg:flex" aria-label="Conversations">
        <Button variant="outline" size="sm" onClick={newChat} disabled={!c.canWrite} data-testid="new-chat"><MessageSquarePlus aria-hidden />New chat</Button>
        <nav className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto" data-testid="conversation-list">
          {convs.data?.items.length === 0 ? <p className="p-2 text-xs text-muted-foreground">No conversations yet.</p> : null}
          {convs.data?.items.map((cv) => (
            <button key={cv.id} onClick={() => setConvId(cv.id)} aria-current={cv.id === convId} className={`truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted ${cv.id === convId ? "bg-muted font-medium" : ""}`}>{cv.title}<span className="block text-xs text-foreground/70 tabular">{new Date(cv.updated_at).toLocaleString([], { timeZone: c.utc ? "UTC" : undefined })}</span></button>
          ))}
        </nav>
      </aside>

      <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card" aria-label="Chat">
        <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-sm">
          <Button variant="outline" size="xs" className="lg:hidden" onClick={newChat} disabled={!c.canWrite}>New chat</Button>
          {rt ? <><RuntimeStatusBadge status={rt.status} /><span className="truncate text-muted-foreground">{rt.name}{rt.served_model ? ` · ${rt.served_model}` : ""}</span>{rt.is_mock ? <MockBadge /> : null}</> : <span className="text-muted-foreground">No runtime</span>}
          {live ? <Badge variant="outline" className="ml-auto" data-testid="transport">{live.transport === "polling" ? "Polling" : "Idle"}</Badge> : null}
        </div>
        {offline ? <Alert className="m-3 border-warn/40" data-testid="offline-banner"><AlertTitle>{reason}</AlertTitle><AlertDescription>You can still read earlier conversations. New prompts need a connected runtime.</AlertDescription></Alert>
          : rt?.status === "stale" ? <Alert className="m-3 border-warn/40"><AlertTitle>Runtime heartbeat is late</AlertTitle><AlertDescription>It may be about to disconnect. Prompts are still accepted.</AlertDescription></Alert> : null}

        {chat.loadingMsgs ? <div className="p-4"><Spinner label="Loading conversation" /></div> : null}
        {!convId && !chat.loadingMsgs ? <div className="p-4"><EmptyState title="Start a conversation">Type a prompt below. The reply is generated by your connected model and streams in as it is produced.</EmptyState></div> : null}
        <MessageScroller deps={[chat.messages.length, live?.text, live?.status]}>
          {chat.messages.map((m) => {
            const own = chat.running && live?.jobId && m.job_id === live.jobId && m.role === "assistant";
            if (own) return null;                       // while streaming, the live bubble represents this job; once finished the persisted message is shown
            return (
              <MessageBubble key={m.id} role={m.role} testId={`msg-${m.role}`} footer={m.role === "assistant" ? <>{m.meta.cancelled ? <span className="mr-2 text-warn">Stopped early</span> : null}<MeasuredLine m={m.meta.measured} /></> : undefined}>
                <RichText text={m.content} />
              </MessageBubble>
            );
          })}
          {live && (liveActive || (live.status !== "succeeded" && !chat.messages.some((m) => m.job_id === live.jobId && m.role === "assistant"))) ? (
            <MessageBubble role="assistant" streaming={!!liveActive} testId="msg-live" footer={
              <span className="tabular flex flex-wrap gap-x-3" data-testid="live-status">
                <span>{live.status.replace("_", " ")}</span>
                {live.firstTokenAt ? <span>first token after {fmt.ms(live.firstTokenAt - live.startedAt)} (as seen in this browser)</span> : <span>waiting for the first token…</span>}
                <span>{live.chunks} chunks</span>{live.attempt > 1 ? <span className="text-warn">attempt {live.attempt}</span> : null}{live.reconnecting ? <span className="text-warn" data-testid="reconnecting">reconnecting…</span> : null}
              </span>}>
              {live.text ? <RichText text={live.text} /> : <span className="text-muted-foreground">…</span>}
            </MessageBubble>
          ) : null}
        </MessageScroller>

        {live && (live.status === "failed" || live.status === "expired") ? (
          <Alert variant="destructive" className="mx-3 mb-2" data-testid="job-failed"><AlertTitle>The request failed</AlertTitle><AlertDescription>{live.errorMessage ?? "The model did not produce a reply."}{live.errorCode ? <span className="tabular text-xs opacity-70"> ({live.errorCode})</span> : null}</AlertDescription></Alert>
        ) : null}
        {err ? <div className="px-3 pb-2"><ErrorState error={err as ApiFailure} title={FRIENDLY[err.code] ?? "Message not sent"} onRetry={chat.clearError} /></div> : null}
        {chat.canRetry ? <div className="px-3 pb-2"><Button size="sm" variant="outline" onClick={() => void chat.retry()} data-testid="retry-button"><RotateCcw aria-hidden />Retry last prompt</Button><span className="ml-2 text-xs text-muted-foreground">Retrying adds the prompt to the conversation again.</span></div> : null}

        <div className="flex flex-wrap items-center gap-3 border-t px-3 py-2 text-xs">
          <Label htmlFor="mt" className="text-xs text-muted-foreground">Max new tokens</Label><Input id="mt" type="number" min={1} max={512} value={maxTokens} onChange={(e) => setMaxTokens(Math.min(512, Math.max(1, Number(e.target.value) || 1)))} className="h-7 w-20" />
          <Label htmlFor="tp" className="text-xs text-muted-foreground">Temperature</Label><Input id="tp" type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(Math.min(2, Math.max(0, Number(e.target.value) || 0)))} className="h-7 w-20" />
        </div>
        <PromptInput onSend={onSend} onCancel={() => void chat.cancel()} running={chat.running} disabled={offline || !c.canWrite || chat.sending || !convId} disabledReason={!c.canWrite ? "Viewers cannot send prompts." : reason} />
      </section>

      <aside className="hidden min-h-0 overflow-y-auto lg:block" aria-label="Request diagnostics">
        <Card size="sm" data-testid="diagnostics">
          <CardHeader><CardTitle>Request diagnostics</CardTitle></CardHeader>
          <CardContent className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs tabular">
            <span className="text-muted-foreground">Source</span><span>{lastMeasured ? (lastMeasured.is_mock ? "Synthetic (mock)" : "Measured by connector") : "—"}</span>
            <span className="text-muted-foreground">TTFT</span><span data-testid="diag-ttft">{fmt.ms(lastMeasured?.ttft_ms)}</span>
            <span className="text-muted-foreground">End-to-end</span><span data-testid="diag-e2e">{fmt.ms(lastMeasured?.e2e_ms)}</span>
            <span className="text-muted-foreground">Stream chunks</span><span>{lastMeasured?.stream_chunks ?? "N/A"}</span>
            <span className="text-muted-foreground">Chunks / s</span><span>{fmt.num(lastMeasured?.chunks_per_s)}</span>
            <span className="text-muted-foreground">Output tokens</span><span title="The streaming API does not report token counts; chunks are not claimed to be tokens.">{lastMeasured?.completion_tokens ?? "not reported"}</span>
            <span className="text-muted-foreground">Model</span><span className="break-all">{lastMeasured?.model ?? rt?.served_model ?? "N/A"}</span>
            <span className="text-muted-foreground">TP / PP</span><span>{lastMeasured?.tp ?? rt?.tp ?? "N/A"} / {lastMeasured?.pp ?? rt?.pp ?? "N/A"}</span>
            <span className="text-muted-foreground">Context</span><span>{lastMeasured?.max_model_len ?? rt?.max_model_len ?? "N/A"}</span>
            <span className="text-muted-foreground">GPUs</span><span>{rt?.capabilities.gpus?.map((g) => g.index).join(", ") || "N/A"}</span>
          </CardContent>
        </Card>
      </aside>
    </div>
  );
}
