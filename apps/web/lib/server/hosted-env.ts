/** True when running on a managed cloud host where development authentication must never be enabled (Vercel, AWS Lambda/Amplify, Railway). */
export function onHostedPlatform(env: Record<string, string | undefined> = process.env): boolean {
  return Boolean(
    env.VERCEL ||
      env.VERCEL_ENV ||
      env.AWS_EXECUTION_ENV ||
      env.AWS_LAMBDA_FUNCTION_NAME ||
      env.RAILWAY_ENVIRONMENT_ID ||
      env.RAILWAY_ENVIRONMENT_NAME ||
      env.RAILWAY_PROJECT_ID ||
      env.RAILWAY_SERVICE_ID,
  );
}

/** Development auth: explicit opt-in, a long secret, and never on a hosted platform. */
export function devAuthEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.ENIGMA_DEV_AUTH === "1" && !onHostedPlatform(env) && (env.ENIGMA_DEV_JWT_SECRET ?? "").length >= 32;
}
