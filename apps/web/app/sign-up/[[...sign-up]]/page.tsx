import Link from "next/link";
import { SignUp } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";
import { CognitoAuth } from "@/components/enigma/cognito-auth";
import { clerkConfigured, cognitoSelected, devAuthAllowed } from "@/lib/server/env";

export default function SignUpPage() {
  if (cognitoSelected()) return <CognitoAuth mode="sign-up" />;
  if (clerkConfigured()) return <main className="grid flex-1 place-items-center p-6"><SignUp /></main>;
  return (
    <main className="grid flex-1 place-items-center p-6">
      <div className="flex max-w-md flex-col gap-3 text-center">
        <h1 className="text-xl font-semibold">Sign-up is not configured</h1>
        <p className="text-sm text-muted-foreground">Set ENIGMA_AUTH_PROVIDER=cognito and the NEXT_PUBLIC_COGNITO_* identifiers (see .env.example).</p>
        {devAuthAllowed() ? <Button render={<Link href="/app" />}>Continue with the local dev login</Button> : null}
      </div>
    </main>
  );
}
