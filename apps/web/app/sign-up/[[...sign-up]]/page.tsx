import Link from "next/link";
import { Button } from "@/components/ui/button";
import { AuthForms } from "@/components/enigma/auth-forms";
import { devAuthAllowed } from "@/lib/server/env";
import { authEnabled, publicSignupOpen } from "@/lib/server/auth-server";

export default async function SignUpPage({ searchParams }: { searchParams: Promise<{ verified?: string }> }) {
  const sp = await searchParams;
  if (authEnabled() && !publicSignupOpen())
    return (
      <main className="grid flex-1 place-items-center p-6">
        <div className="flex max-w-md flex-col gap-3 text-center" data-testid="signup-closed">
          <h1 className="text-xl font-semibold">Enigma is in private beta</h1>
          <p className="text-sm text-muted-foreground">Sign-up is closed for now. If you already have an account, sign in.</p>
          <Button render={<Link href="/sign-in" />}>Go to sign in</Button>
        </div>
      </main>
    );
  if (authEnabled())
    return <AuthForms mode="sign-up" notice={sp.verified === "1" ? "Email verified. Sign in with your password." : undefined} />;
  return (
    <main className="grid flex-1 place-items-center p-6">
      <div className="flex max-w-md flex-col gap-3 text-center">
        <h1 className="text-xl font-semibold">Sign-in is not configured</h1>
        <p className="text-sm text-muted-foreground">Set ENIGMA_AUTH=on, ENIGMA_PUBLIC_ORIGIN, BETTER_AUTH_SECRET and ENIGMA_AUTH_DB_DSN (see .env.example).</p>
        {devAuthAllowed() ? <Button render={<Link href="/app" />}>Continue with the local dev login</Button> : null}
      </div>
    </main>
  );
}
