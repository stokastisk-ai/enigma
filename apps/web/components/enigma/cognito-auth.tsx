"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Mode = "sign-in" | "sign-up";

type Provider = "cognito" | "neon";
const base = (p: Provider) => (p === "neon" ? "/api/auth/neon" : "/api/auth");

async function post(action: string, body: Record<string, string>, provider: Provider = "cognito") {
  const r = await fetch(`${base(provider)}/${action}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, message: j?.error?.message as string | undefined, needsConfirm: Boolean(j?.needsConfirm || j?.needsVerify) };
}

/**
 * Email + password forms for Amazon Cognito (default) or Neon Managed Better Auth (`provider="neon"`). Tokens are set as httpOnly cookies by the
 * server; this component never sees them. `alt` links to the other provider's form while both are enabled.
 */
export function CognitoAuth({ mode, provider = "cognito", alt }: { mode: Mode; provider?: Provider; alt?: { href: string; label: string } }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (confirming) {
        const c = await post(provider === "neon" ? "verify" : "confirm", { email, code }, provider);
        if (!c.ok) return setError(c.message ?? "Could not confirm.");
        const s = await post("sign-in", { email, password }, provider);
        if (!s.ok) return setError(s.message ?? "Could not sign in.");
        return router.replace("/app");
      }
      const r = await post(mode, { email, password }, provider);
      if (r.ok && mode === "sign-up") return r.needsConfirm || provider === "cognito" ? setConfirming(true) : router.replace("/sign-in?provider=neon");
      if (r.ok) return router.replace("/app");
      if (r.needsConfirm) return setConfirming(true);
      setError(r.message ?? "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid flex-1 place-items-center p-6">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border bg-card p-6" data-testid={provider === "neon" ? "neon-form" : "cognito-form"}>
        <h1 className="text-xl font-semibold">{confirming ? "Confirm your email" : mode === "sign-in" ? "Sign in to Enigma" : "Create your Enigma account"}</h1>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} disabled={confirming} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} required minLength={provider === "neon" && mode === "sign-up" ? 12 : 8} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {confirming ? (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="code">{provider === "neon" ? "Verification code (check your email)" : "Confirmation code"}</Label>
            <Input id="code" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" required value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
        ) : null}
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        <Button type="submit" disabled={busy}>{busy ? "Please wait…" : confirming ? "Confirm and continue" : mode === "sign-in" ? "Sign in" : "Create account"}</Button>
        {!confirming ? (
          <p className="text-center text-sm text-muted-foreground">
            {mode === "sign-in" ? <>No account? <Link className="underline" href={provider === "neon" ? "/sign-up?provider=neon" : "/sign-up"}>Sign up</Link></> : <>Have an account? <Link className="underline" href={provider === "neon" ? "/sign-in?provider=neon" : "/sign-in"}>Sign in</Link></>}
          </p>
        ) : null}
        {!confirming && alt ? <p className="text-center text-xs text-muted-foreground"><Link className="underline" href={alt.href} data-testid="auth-alt">{alt.label}</Link></p> : null}
      </form>
    </main>
  );
}

export function CognitoSignOut() {
  const router = useRouter();
  return (
    <div className="px-2">
      <Button variant="ghost" size="sm" data-testid="sign-out" onClick={async () => { await Promise.allSettled([post("sign-out", {}), post("sign-out", {}, "neon")]); router.replace("/"); router.refresh(); }}>Sign out</Button>
    </div>
  );
}
