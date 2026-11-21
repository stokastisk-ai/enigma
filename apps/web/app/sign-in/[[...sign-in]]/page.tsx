import Link from "next/link";
import { SignIn } from "@clerk/nextjs";
import { Button } from "@/components/ui/button";
import { clerkConfigured, devAuthAllowed } from "@/lib/server/env";

export default function SignInPage() {
  if (clerkConfigured()) return <main className="grid flex-1 place-items-center p-6"><SignIn /></main>;
  return (
    <main className="grid flex-1 place-items-center p-6">
      <div className="flex max-w-md flex-col gap-3 text-center">
        <h1 className="text-xl font-semibold">Sign-in is not configured</h1>
        <p className="text-sm text-muted-foreground">Set NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY (see .env.example).</p>
        {devAuthAllowed() ? <Button render={<Link href="/app" />}>Continue with the local dev login</Button> : null}
      </div>
    </main>
  );
}
