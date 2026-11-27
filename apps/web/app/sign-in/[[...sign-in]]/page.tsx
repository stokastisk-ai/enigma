import Link from "next/link";
import { Button } from "@/components/ui/button";
import { AuthForms } from "@/components/enigma/auth-forms";
import { devAuthAllowed } from "@/lib/server/env";
import { authEnabled } from "@/lib/server/auth-server";

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ verified?: string }> }) {
  const sp = await searchParams;
  if (authEnabled())
    return <AuthForms mode="sign-in" notice={sp.verified === "1" ? "Email verified. Sign in with your password." : undefined} />;
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
