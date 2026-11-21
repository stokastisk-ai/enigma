"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Mode = "sign-in" | "sign-up";

async function post(action: string, body: Record<string, string>) {
  const r = await fetch(`/api/auth/${action}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, message: j?.error?.message as string | undefined, needsConfirm: Boolean(j?.needsConfirm) };
}

/** Email + password forms for Amazon Cognito. Tokens are set as httpOnly cookies by the server; this component never sees them. */
export function CognitoAuth({ mode }: { mode: Mode }) {
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
        const c = await post("confirm", { email, code });
        if (!c.ok) return setError(c.message ?? "Could not confirm.");
        const s = await post("sign-in", { email, password });
        if (!s.ok) return setError(s.message ?? "Could not sign in.");
        return router.replace("/app");
      }
      const r = await post(mode, { email, password });
      if (r.ok && mode === "sign-up") return setConfirming(true);
      if (r.ok) return router.replace("/app");
      if (r.needsConfirm) return setConfirming(true);
      setError(r.message ?? "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid flex-1 place-items-center p-6">
      <form onSubmit={submit} className="flex w-full max-w-sm flex-col gap-4 rounded-xl border bg-card p-6" data-testid="cognito-form">
        <h1 className="text-xl font-semibold">{confirming ? "Confirm your email" : mode === "sign-in" ? "Sign in to Enigma" : "Create your Enigma account"}</h1>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} disabled={confirming} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" autoComplete={mode === "sign-in" ? "current-password" : "new-password"} required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {confirming ? (
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="code">Confirmation code</Label>
            <Input id="code" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" required value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
        ) : null}
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
        <Button type="submit" disabled={busy}>{busy ? "Please wait…" : confirming ? "Confirm and continue" : mode === "sign-in" ? "Sign in" : "Create account"}</Button>
        {!confirming ? (
          <p className="text-center text-sm text-muted-foreground">
            {mode === "sign-in" ? <>No account? <Link className="underline" href="/sign-up">Sign up</Link></> : <>Have an account? <Link className="underline" href="/sign-in">Sign in</Link></>}
          </p>
        ) : null}
      </form>
    </main>
  );
}

export function CognitoSignOut() {
  const router = useRouter();
  return (
    <div className="px-2">
      <Button variant="ghost" size="sm" data-testid="sign-out" onClick={async () => { await post("sign-out", {}); router.replace("/"); router.refresh(); }}>Sign out</Button>
    </div>
  );
}
