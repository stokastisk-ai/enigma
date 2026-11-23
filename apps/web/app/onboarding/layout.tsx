import { requirePageSession } from "@/lib/server/page-guard";

// The onboarding page is a client component, so its server-side session check lives in this layout.
export default async function OnboardingLayout({ children }: { children: React.ReactNode }) {
  await requirePageSession();
  return children;
}
