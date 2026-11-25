"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Mode = "sign-in" | "sign-up";
type Step = "form" | "verify";
type Reply = { ok: boolean; status: number; code?: string; message?: string; needsVerify: boolean; signedIn: boolean };

const RESEND_COOLDOWN_S = 60;

async function post(action: string, body: Record<string, string>): Promise<Reply> {
  try {
    const r = await fetch(`/api/auth/neon/${action}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, code: j?.error?.code, message: j?.error?.message, needsVerify: Boolean(j?.needsVerify), signedIn: Boolean(j?.signedIn) };
  } catch {
    return { ok: false, status: 0, code: "network", message: "Could not reach Enigma. Check your connection and try again.", needsVerify: false, signedIn: false };
  }
}

/**
 * Email + password forms for Neon Managed Better Auth (beta), with the e-mail verification code step.
 * Neon e-mails a 6-digit code (valid 15 minutes). Sessions are httpOnly cookies set by the server; this component never sees a token.
 * Messages are deliberately the same for new and existing addresses (no account enumeration).
 */
export function NeonAuth({ mode, notice: initialNotice }: { mode: Mode; notice?: string }) {
  const router = useRouter();
  const [step, setStep] = useState<Step>("form");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(initialNotice ?? null);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const toVerify = (msg: string) => {
    setStep("verify");
    setError(null);
    setNotice(msg);
    setCode("");
    setCooldown(RESEND_COOLDOWN_S);
  };

  async function submitForm(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await post(mode, { email, password });
      if (r.ok && mode === "sign-in") return router.replace("/app");
      if (r.ok && mode === "sign-up") {
        if (r.needsVerify) return toVerify("If this address can be registered, we sent a 6-digit code to it. It can take a minute; check Spam and Promotions. The code expires in 15 minutes.");
        return router.replace("/sign-in");
      }
      if (r.needsVerify) return toVerify("Your email is not verified yet. We sent a new 6-digit code if one was not already sent in the last minute.");
      setError(r.message ?? "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await post("verify", { email, code });
      if (r.ok) {
        if (r.signedIn) return router.replace("/app");
        if (password) {
          const s = await post("sign-in", { email, password });
          if (s.ok) return router.replace("/app");
        }
        setStep("form");
        setNotice("Email verified. Sign in with your password.");
        if (mode === "sign-up") router.replace("/sign-in?verified=1");
        return;
      }
      if (r.code === "already_verified") {
        setStep("form");
        setNotice("This email is already verified. Sign in with your password.");
        if (mode === "sign-up") router.replace("/sign-in");
        return;
      }
      setError(r.message ?? "Could not verify the code.");
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    if (cooldown > 0 || busy || !email) return;
    setBusy(true);
    setError(null);
    try {
      const r = await post("resend", { email });
      if (r.ok) {
        setNotice("If an account is waiting for verification, a new code is on its way. Check Spam and Promotions.");
        setCooldown(RESEND_COOLDOWN_S);
      } else {
        if (r.status === 429) setCooldown(RESEND_COOLDOWN_S);
        setError(r.message ?? "Could not send a new code.");
      }
    } finally {
      setBusy(false);
    }
  }

  const heading = step === "verify" ? "Verify your email" : mode === "sign-in" ? "Sign in to Enigma" : "Create your Enigma account";

  return (
    <main className="grid flex-1 place-items-center p-6">
      <form onSubmit={step === "verify" ? submitCode : submitForm} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border bg-card p-6" data-testid={step === "verify" ? "neon-verify-form" : "neon-form"}>
        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold">{heading}</h1>
          <p className="text-xs text-muted-foreground" data-testid="auth-provider-label">New Enigma account (beta). Verification codes are sent by email.</p>
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>

        {step === "form" ? (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">Password</Label>
            <Input id="password" type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} required minLength={mode === "sign-up" ? 12 : 1} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} />
            {mode === "sign-up" ? <p className="text-xs text-muted-foreground">At least 12 characters. Avoid common passwords and your email name.</p> : null}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="code">Verification code</Label>
            <Input id="code" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </div>
        )}

        <div aria-live="polite">{notice ? <p className="text-sm text-muted-foreground" data-testid="auth-notice">{notice}</p> : null}</div>
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}

        <Button type="submit" disabled={busy}>{busy ? "Please wait…" : step === "verify" ? "Verify and continue" : mode === "sign-in" ? "Sign in" : "Create account"}</Button>

        {step === "verify" ? (
          <div className="flex flex-col gap-1 text-center text-sm text-muted-foreground">
            <button type="button" className="underline disabled:no-underline disabled:opacity-60" disabled={cooldown > 0 || busy || !email} onClick={resend} data-testid="resend-code">
              {cooldown > 0 ? `Send a new code in ${cooldown}s` : "Send a new code"}
            </button>
            <button type="button" className="underline" onClick={() => { setStep("form"); setError(null); setNotice(null); }}>Use a different email or go back</button>
          </div>
        ) : (
          <div className="flex flex-col gap-1 text-center text-sm text-muted-foreground">
            <p>{mode === "sign-in" ? <>No account? <Link className="underline" href="/sign-up">Sign up</Link></> : <>Have an account? <Link className="underline" href="/sign-in">Sign in</Link></>}</p>
            <button type="button" className="underline" onClick={() => { setStep("verify"); setError(null); setNotice("Enter the email you registered with and the 6-digit code we sent."); }} data-testid="have-code">Already have a code? Verify your email</button>
          </div>
        )}
      </form>
    </main>
  );
}

export function NeonSignOut() {
  const router = useRouter();
  return (
    <div className="px-2">
      <Button variant="ghost" size="sm" data-testid="sign-out" onClick={async () => {
        await fetch("/api/auth/neon/sign-out", { method: "POST", headers: { "content-type": "application/json" }, body: "{}" }).catch(() => {});
        router.replace("/");
        router.refresh();
      }}>Sign out</Button>
    </div>
  );
}
