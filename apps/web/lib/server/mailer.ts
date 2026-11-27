
/**
 * Verification-code e-mail over SMTP (server only). Configured by ENIGMA_SMTP_URL (smtps://user:pass@host:465 or smtp://...:587 with STARTTLS required)
 * and ENIGMA_MAIL_FROM. Unconfigured = sending throws, so sign-up fails closed with "auth_unavailable"; existing verified users can still sign in.
 * The code is never logged; failures are logged by SMTP response code only.
 */
export function mailConfigured(env: Record<string, string | undefined> = process.env): boolean {
  try {
    const u = new URL(env.ENIGMA_SMTP_URL ?? "");
    return (u.protocol === "smtps:" || u.protocol === "smtp:") && Boolean(u.hostname) && /^[^\s@<>]+@[^\s@<>]+$/.test(env.ENIGMA_MAIL_FROM ?? "");
  } catch {
    return false;
  }
}

let transport: import("nodemailer").Transporter | null = null;

export async function sendOtpEmail(to: string, otp: string, type: string): Promise<void> {
  if (!mailConfigured()) throw new Error("mail_not_configured");
  if (!transport) {
    const nodemailer = await import("nodemailer");
    const u = new URL(process.env.ENIGMA_SMTP_URL!);
    transport = nodemailer.createTransport({
      host: u.hostname,
      port: Number(u.port || (u.protocol === "smtps:" ? 465 : 587)),
      secure: u.protocol === "smtps:",
      requireTLS: true,                                   // never send a code over a plaintext SMTP session
      auth: u.username ? { user: decodeURIComponent(u.username), pass: decodeURIComponent(u.password) } : undefined,
      connectionTimeout: 10_000,
      socketTimeout: 15_000,
    });
  }
  const what = type === "forget-password" ? "reset your password" : "verify your email";
  try {
    await transport.sendMail({
      from: process.env.ENIGMA_MAIL_FROM,
      to,
      subject: "Your Enigma code",
      text: `Your Enigma code is ${otp}. Use it to ${what}. It expires in 10 minutes. If you did not request it, ignore this email.`,
    });
  } catch (e) {
    const code = String((e as { responseCode?: number; code?: string })?.responseCode ?? (e as { code?: string })?.code ?? "none").replace(/[^A-Za-z0-9_]/g, "");
    console.warn(`[enigma.mail] send_failed code=${code}`);
    throw new Error("mail_send_failed");
  }
}
