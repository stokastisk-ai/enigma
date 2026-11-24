import Link from "next/link";
import { SignIn } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";
import { CognitoAuth } from "@/components/enigma/cognito-auth";
import { NeonAuth } from "@/components/enigma/neon-auth";
import { clerkConfigured, cognitoSelected, devAuthAllowed } from "@/lib/server/env";
import { neonAuthEnabled } from "@/lib/server/neon-auth";

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ provider?: string; verified?: string }> }) {
  const neon = neonAuthEnabled();
  const cognito = cognitoSelected();
  // Cognito stays the default while it is selected; `?provider=neon` opens the Neon Managed Better Auth form. Neon-only deployments always show it.
  const sp = await searchParams;
  if (neon && (sp.provider === "neon" || (!cognito && !clerkConfigured())))
    return <NeonAuth mode="sign-in" notice={sp.verified === "1" ? "Email verified. Sign in with your password." : undefined} alt={cognito ? { href: "/sign-in", label: "Use your existing Amazon Cognito account" } : undefined} />;
  if (cognito) return <CognitoAuth mode="sign-in" alt={neon ? { href: "/sign-in?provider=neon", label: "Use a new Enigma account (beta)" } : undefined} />;
  if (clerkConfigured()) return <main className="grid flex-1 place-items-center p-6"><SignIn /></main>;
  return (
    <main className="grid flex-1 place-items-center p-6">
      <div className="flex max-w-md flex-col gap-3 text-center">
        <h1 className="text-xl font-semibold">Sign-in is not configured</h1>
        <p className="text-sm text-muted-foreground">Set ENIGMA_AUTH_PROVIDER=cognito and the NEXT_PUBLIC_COGNITO_* identifiers (see .env.example).</p>
        {devAuthAllowed() ? <Button render={<Link href="/app" />}>Continue with the local dev login</Button> : null}
      </div>
    </main>
  );
}
