import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { clerkConfigured } from "@/lib/server/env";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Enigma — GPU inference console", template: "%s · Enigma" },
  description: "Chat with your own GPU-hosted vLLM model and watch real GPU and inference metrics.",
};

// Auth configuration is read from the runtime environment, so nothing here may be prerendered at build time.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const body = (
    <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
      <TooltipProvider delay={150}>{children}</TooltipProvider>
      <Toaster richColors closeButton />
    </ThemeProvider>
  );
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{clerkConfigured() ? <ClerkProvider>{body}</ClerkProvider> : body}</body>
    </html>
  );
}
