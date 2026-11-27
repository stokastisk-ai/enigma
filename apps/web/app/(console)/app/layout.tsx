import { ConsoleShell } from "@/components/enigma/console-shell";
import { AuthSignOut } from "@/components/enigma/auth-forms";
import { devAuthAllowed, publicApiUrl } from "@/lib/server/env";
import { authEnabled } from "@/lib/server/auth-server";
import { requirePageSession } from "@/lib/server/page-guard";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  await requirePageSession(); // resource-level check; proxy.ts is only the first line of defence
  const realAuth = authEnabled();
  return (
    <ConsoleShell publicApiUrl={publicApiUrl()} devAuth={!realAuth && devAuthAllowed()} authSlot={realAuth ? <AuthSignOut /> : null}>
      {children}
    </ConsoleShell>
  );
}
