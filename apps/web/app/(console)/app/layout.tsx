import { UserButton } from "@clerk/nextjs";
import { ConsoleShell } from "@/components/enigma/console-shell";
import { RealtimeBridge } from "@/components/enigma/realtime-bridge";
import { CognitoSignOut } from "@/components/enigma/cognito-auth";
import { clerkConfigured, cognitoSelected, devAuthAllowed } from "@/lib/server/env";

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const clerk = clerkConfigured();
  const sbUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  // Supabase now calls this the "publishable" key; the legacy anon-key name is still accepted. Both are public by design (RLS protects the data).
  const sbKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const shell = (
    <ConsoleShell devAuth={!clerk && devAuthAllowed()} authSlot={clerk ? <div className="px-2"><UserButton /></div> : cognitoSelected() ? <CognitoSignOut /> : null}>
      {children}
    </ConsoleShell>
  );
  // Realtime needs the Clerk session token, so it is only enabled when Clerk and the public Supabase settings exist; otherwise polling is used.
  return clerk && sbUrl && sbKey ? <RealtimeBridge url={sbUrl} anonKey={sbKey}>{shell}</RealtimeBridge> : shell;
}
