/**
 * Sign-up password policy (server side, enforced by the BFF before anything reaches Neon). Length first (NIST SP 800-63B style) plus the cheap
 * checks that catch the worst choices: no composition rules that push users towards "Password1!".
 * Neon's own endpoint enforces only its default minimum, so this is a BFF-level policy, not a service-wide one.
 */
export const PASSWORD_MIN = 12;
export const PASSWORD_MAX = 128;

// A deliberately small blocklist of the passwords that satisfy a 12-character rule most often. Not a substitute for a breach-corpus check.
const COMMON = new Set([
  "password1234", "password12345", "123456789012", "1234567890123", "qwertyuiop12", "qwertyuiopas", "iloveyou1234", "letmein12345",
  "administrator", "welcome12345", "changeme1234", "abcdefghijkl", "enigma123456", "enigmaenigma",
]);

/** Returns a user-facing problem description, or null when the password is acceptable. Never echoes the password. */
export function passwordProblem(email: string, password: string): string | null {
  if (password.length < PASSWORD_MIN) return `Use at least ${PASSWORD_MIN} characters.`;
  if (password.length > PASSWORD_MAX) return `Use at most ${PASSWORD_MAX} characters.`;
  if (new Set(password).size < 6) return "Use more different characters.";
  const lower = password.toLowerCase();
  if (COMMON.has(lower)) return "That password is too common.";
  const local = email.split("@")[0]?.toLowerCase() ?? "";
  if (local.length >= 4 && lower.includes(local)) return "Do not include your email name in the password.";
  if (/^(.{1,3})\1+$/.test(password)) return "Do not use a repeating pattern.";
  return null;
}
