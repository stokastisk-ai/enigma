"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiFailure, newKey } from "@/lib/api";
import { TERMINAL, type JobEvent, type JobView, type Message } from "@/lib/types";

export type Transport = "idle" | "polling";
export type Live = {
  jobId: string; status: JobView["status"]; text: string; chunks: number; startedAt: number; firstTokenAt: number | null; attempt: number;
  result: JobView["result"]; errorCode: string | null; errorMessage: string | null; reconnecting: boolean; transport: Transport;
};
export type SendParams = { runtimeId: string; content: string; maxTokens: number; temperature: number };

const POLL_FAST = 400;

/**
 * Streaming chat for one conversation.
 *  - Deltas are applied strictly in event_seq order, de-duplicated (Realtime + polling can both deliver), and a `reset` event clears the
 *    partial text of an abandoned attempt.
 *  - Authenticated polling of `after_seq` is the transport.
 *  - The persisted assistant message replaces the live bubble once the job is terminal.
 */
export function useChat(conversationId: string | null) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [sending, setSending] = useState(false);
  const [loadingMsgs, setLoadingMsgs] = useState(false);

  const seqRef = useRef(0);               // highest contiguous event_seq applied
  const buf = useRef<Map<number, JobEvent>>(new Map());   // out-of-order events waiting for their predecessors
  const textRef = useRef("");
  const chunksRef = useRef(0);
  const jobRef = useRef<string | null>(null);
  const stopRef = useRef<() => void>(() => {});
  const lastSend = useRef<SendParams | null>(null);
  const convRef = useRef(conversationId);
  const [hasLastSend, setHasLastSend] = useState(false);
  const [prevId, setPrevId] = useState(conversationId);
  // Reset per-conversation state while rendering (React's recommended pattern) instead of in an effect.
  if (prevId !== conversationId) { setPrevId(conversationId); setLive(null); setError(null); setMessages([]); setLoadingMsgs(!!conversationId); }
  useEffect(() => { convRef.current = conversationId; }, [conversationId]);

  const loadMessages = useCallback(async (id: string, signal?: AbortSignal) => {
    const r = await api<{ items: Message[] }>(`conversations/${id}/messages`, { signal });
    if (convRef.current === id) setMessages(r.items);
    return r.items;
  }, []);

  useEffect(() => {
    if (!conversationId) return;
    const ctl = new AbortController();
    loadMessages(conversationId, ctl.signal).catch((e) => { if ((e as Error).name !== "AbortError") setError(e as ApiFailure); }).finally(() => { if (!ctl.signal.aborted) setLoadingMsgs(false); });
    return () => { ctl.abort(); stopRef.current(); };        // leaving a conversation stops following its job
  }, [conversationId, loadMessages]);

  const applyEvents = useCallback((events: JobEvent[]) => {
    for (const e of events) if (e.seq > seqRef.current) buf.current.set(e.seq, e);
    let advanced = false;
    // events must be consumed contiguously; anything beyond a gap waits (polling will fill it)
    while (true) {
      const next = buf.current.get(seqRef.current + 1);
      if (!next) break;
      buf.current.delete(next.seq);
      seqRef.current = next.seq;
      if (next.kind === "reset") { textRef.current = ""; chunksRef.current = 0; }
      else if (next.kind === "delta" && next.text) { textRef.current += next.text; chunksRef.current += 1; }
      advanced = true;
    }
    return advanced;
  }, []);

  const follow = useCallback((jobId: string, convId: string) => {
    stopRef.current();
    jobRef.current = jobId; seqRef.current = 0; buf.current.clear(); textRef.current = ""; chunksRef.current = 0;
    let stopped = false;
    const transport: Transport = "polling";
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const ctl = new AbortController();
    const startedAt = Date.now();
    let firstTokenAt: number | null = null;

    const poll = async () => {
      if (stopped) return;
      try {
        const v = await api<JobView>(`jobs/${jobId}?after_seq=${seqRef.current}`, { signal: ctl.signal });
        if (stopped) return;
        failures = 0;
        const adv = applyEvents(v.events.map((e) => e));
        if (adv && firstTokenAt === null && textRef.current) firstTokenAt = Date.now();
        setLive({ jobId, status: v.status, text: textRef.current, chunks: chunksRef.current, startedAt, firstTokenAt, attempt: v.attempt, result: v.result, errorCode: v.error_code, errorMessage: v.error_message, reconnecting: false, transport });
        if (TERMINAL.includes(v.status) && buf.current.size === 0 && seqRef.current >= v.last_event_seq) {
          stopped = true;
          await loadMessages(convId).catch(() => {});
          setLive((l) => (l ? { ...l, transport: "idle" } : l));
          return;
        }
        timer = setTimeout(poll, POLL_FAST);
      } catch (e) {
        if (stopped || (e as Error).name === "AbortError") return;
        failures += 1;
        const f = e as ApiFailure;
        if (f.status === 404 || f.status === 401) { stopped = true; setError(f); return; }
        setLive((l) => (l ? { ...l, reconnecting: true } : l));
        timer = setTimeout(poll, Math.min(5000, 500 * 2 ** failures));      // backoff while the network or API is down
      }
    };

    setLive({ jobId, status: "pending", text: "", chunks: 0, startedAt, firstTokenAt: null, attempt: 0, result: null, errorCode: null, errorMessage: null, reconnecting: false, transport });
    void poll();
    stopRef.current = () => { stopped = true; clearTimeout(timer); ctl.abort(); };
  }, [applyEvents, loadMessages]);

  useEffect(() => () => stopRef.current(), []);

  const send = useCallback(async (p: SendParams): Promise<boolean> => {
    if (!conversationId || sending) return false;
    setSending(true); setError(null); lastSend.current = p; setHasLastSend(true);
    const key = newKey();
    setMessages((m) => [...m, { id: `pending-${key}`, role: "user", content: p.content, job_id: null, created_at: new Date().toISOString(), meta: {} }]);
    try {
      const r = await api<{ job_id: string }>(`conversations/${conversationId}/jobs`, {
        method: "POST", headers: { "Idempotency-Key": key },
        body: { runtime_id: p.runtimeId, content: p.content, max_tokens: p.maxTokens, temperature: p.temperature },
      });
      await loadMessages(conversationId).catch(() => {});
      follow(r.job_id, conversationId);
      return true;
    } catch (e) {
      setMessages((m) => m.filter((x) => x.id !== `pending-${key}`));      // the prompt was not accepted: do not leave a phantom turn
      setError(e as ApiFailure);
      return false;
    } finally { setSending(false); }
  }, [conversationId, sending, follow, loadMessages]);

  const cancel = useCallback(async () => {
    if (!live) return;
    try { await api(`jobs/${live.jobId}/cancel`, { method: "POST" }); setLive((l) => (l && !TERMINAL.includes(l.status) ? { ...l, status: "cancel_requested" } : l)); }
    catch (e) { setError(e as ApiFailure); }
  }, [live]);

  const retry = useCallback(() => (lastSend.current ? send(lastSend.current) : Promise.resolve(false)), [send]);
  const running = !!live && !TERMINAL.includes(live.status);
  return { messages, live, running, error, sending, loadingMsgs, send, cancel, retry, canRetry: hasLastSend && !!live && TERMINAL.includes(live.status) && live.status !== "succeeded", clearError: () => setError(null) };
}
