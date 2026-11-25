import Link from "next/link";
import { Button } from "@/components/ui/button";
import { NeonAuth } from "@/components/enigma/neon-auth";
import { devAuthAllowed } from "@/lib/server/env";
import { neonAuthEnabled } from "@/lib/server/neon-auth";

export default async function SignUpPage({ searchParams }: { searchParams: Promise<{ verified?: string }> }) {
  const sp = await searchParams;
  if (neonAuthEnabled())
    return <NeonAuth mode="sign-up" notice={sp.verified === "1" ? "Email verified. Sign in with your password." : undefined} />;
  return (
    <main className="grid flex-1 place-items-center p-6">
      <div className="flex max-w-md flex-col gap-3 text-center">
        <h1 className="text-xl font-semibold">Sign-in is not configured</h1>
        <p className="text-sm text-muted-foreground">Set ENIGMA_NEON_AUTH=on, NEON_AUTH_BASE_URL and NEON_AUTH_COOKIE_SECRET (see .env.example).</p>
        {devAuthAllowed() ? <Button render={<Link href="/app" />}>Continue with the local dev login</Button> : null}
      </div>
    </main>
  );
}
