import { ConsoleShell } from "@/components/enigma/console-shell";
import { NeonSignOut } from "@/components/enigma/neon-auth";
import { devAuthAllowed, publicApiUrl } from "@/lib/server/env";
import { neonAuthEnabled } from "@/lib/server/neon-auth";
import { requirePageSession } from "@/lib/server/page-guard";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  await requirePageSession(); // resource-level check; proxy.ts is only the first line of defence
  const neon = neonAuthEnabled();
  return (
    <ConsoleShell publicApiUrl={publicApiUrl()} devAuth={!neon && devAuthAllowed()} authSlot={neon ? <NeonSignOut /> : null}>
      {children}
    </ConsoleShell>
  );
}
