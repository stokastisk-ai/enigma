// Run with: npm run test:unit   (Node's built-in runner; no extra dependencies). Uses LOCALLY generated keys: it proves our claim
// handling, NOT the behaviour of a real Cognito user pool (that stays BLOCKED until one exists).
import test from "node:test";
import assert from "node:assert/strict";
import { createSign, generateKeyPairSync } from "node:crypto";
import {
  _clearJwksCache, CognitoError, cognitoConfig, confirmSignUp, issuerOf, publicError, refresh, sameOrigin, signIn, signUp, verifyAccessToken, type CognitoConfig,
} from "./cognito.ts";

const env = { NEXT_PUBLIC_COGNITO_REGION: "us-east-1", NEXT_PUBLIC_COGNITO_USER_POOL_ID: "us-east-1_TESTPOOL", NEXT_PUBLIC_COGNITO_CLIENT_ID: "testclientid123" };
const cfg = cognitoConfig(env) as CognitoConfig;
const mk = () => generateKeyPairSync("rsa", { modulusLength: 2048 });
const good = mk();
const other = mk();
const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwkOf = (k: ReturnType<typeof mk>, kid: string) => ({ ...k.publicKey.export({ format: "jwk" }), kid, alg: "RS256", use: "sig" });

function mint(over: Record<string, unknown> = {}, opts: { key?: ReturnType<typeof mk>; kid?: string; alg?: string } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const payload = { sub: "u-1", iss: issuerOf(cfg), token_use: "access", client_id: cfg.clientId, iat: now, exp: now + 600, ...over };
  const clean: Record<string, unknown> = { ...payload };
  for (const k of Object.keys(clean)) if (clean[k] === undefined) delete clean[k];
  const head = b64({ alg: opts.alg ?? "RS256", kid: opts.kid ?? "k1", typ: "JWT" });
  const body = b64(clean);
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign((opts.key ?? good).privateKey).toString("base64url");
  return `${head}.${body}.${sig}`;
}
const jwksFetch = (calls?: { n: number }): typeof fetch =>
  (async () => {
    if (calls) calls.n++;
    return new Response(JSON.stringify({ keys: [jwkOf(good, "k1")] }), { status: 200 });
  }) as typeof fetch;

test.beforeEach(() => _clearJwksCache());

test("config: rejects malformed identifiers and ignores the test endpoint in production / for non-loopback hosts", () => {
  assert.equal(cognitoConfig({ ...env, NEXT_PUBLIC_COGNITO_REGION: "nope" }), null);
  assert.equal(cognitoConfig({}), null);
  assert.equal(cognitoConfig({ ...env, ENIGMA_COGNITO_TEST_ENDPOINT: "http://127.0.0.1:9", NODE_ENV: "production" })!.endpoint, undefined);
  assert.equal(cognitoConfig({ ...env, ENIGMA_COGNITO_TEST_ENDPOINT: "http://evil.example", NODE_ENV: "development" })!.endpoint, undefined);
  assert.equal(cognitoConfig({ ...env, ENIGMA_COGNITO_TEST_ENDPOINT: "http://127.0.0.1:9", NODE_ENV: "development" })!.endpoint, "http://127.0.0.1:9");
});

test("valid access token verifies", async () => {
  assert.deepEqual((await verifyAccessToken(mint(), cfg, jwksFetch()))?.sub, "u-1");
});

for (const [name, tok] of [
  ["wrong issuer", () => mint({ iss: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OTHER" })],
  ["wrong app client", () => mint({ client_id: "someoneelse1234" })],
  ["id token", () => mint({ token_use: "id" })],
  ["expired", () => mint({ exp: Math.floor(Date.now() / 1000) - 5 })],
  ["missing sub", () => mint({ sub: undefined })],
  ["unknown signing key", () => mint({}, { key: other })],
  ["unknown kid", () => mint({}, { kid: "nope" })],
  ["alg none", () => `${b64({ alg: "none", kid: "k1" })}.${b64({ sub: "u", iss: issuerOf(cfg), token_use: "access", client_id: cfg.clientId, exp: 9e9 })}.`],
  ["HS256 forged with public key text", () => mint({}, { alg: "HS256" })],
  ["garbage", () => "not.a.jwt"],
] as const) {
  test(`rejects: ${name}`, async () => assert.equal(await verifyAccessToken(tok(), cfg, jwksFetch()), null));
}

test("JWKS is cached, and refreshed once for an unknown kid (key rotation)", async () => {
  const calls = { n: 0 };
  const f = jwksFetch(calls);
  await verifyAccessToken(mint(), cfg, f);
  await verifyAccessToken(mint(), cfg, f);
  assert.equal(calls.n, 1);
  await verifyAccessToken(mint({}, { kid: "rotated" }), cfg, f);
  assert.equal(calls.n, 2);
});

test("IDP requests: sign-in/refresh/sign-up/confirm shape; secrets only in the body; error mapping hides account existence", async () => {
  const seen: { target: string; body: Record<string, unknown> }[] = [];
  const f = (async (_u: string, init: RequestInit) => {
    seen.push({ target: (init.headers as Record<string, string>)["x-amz-target"], body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify({ AuthenticationResult: { AccessToken: "AT", RefreshToken: "RT", ExpiresIn: 3600 } }), { status: 200 });
  }) as unknown as typeof fetch;
  assert.deepEqual(await signIn(cfg, "a@b.co", "pw", f), { accessToken: "AT", refreshToken: "RT", expiresIn: 3600 });
  assert.equal(seen[0].body.AuthFlow, "USER_PASSWORD_AUTH");
  assert.equal(seen[0].body.ClientId, cfg.clientId);
  assert.equal((await refresh(cfg, "RT0", (async () => new Response(JSON.stringify({ AuthenticationResult: { AccessToken: "AT2", ExpiresIn: 3600 } }))) as unknown as typeof fetch)).refreshToken, "RT0");
  await signUp(cfg, "a@b.co", "pw", f);
  await confirmSignUp(cfg, "a@b.co", "123456", f);
  assert.deepEqual(seen.map((s) => String(s.target).split(".").pop()), ["InitiateAuth", "SignUp", "ConfirmSignUp"]);

  const fail = (type: string) => (async () => new Response(JSON.stringify({ __type: type }), { status: 400 })) as unknown as typeof fetch;
  const a = await signIn(cfg, "x@y.z", "p", fail("com.amazonaws#UserNotFoundException")).catch((e) => publicError(e));
  const b = await signIn(cfg, "x@y.z", "p", fail("NotAuthorizedException")).catch((e) => publicError(e));
  assert.deepEqual(a, b); // identical response for unknown user and wrong password
  assert.equal((a as { code: string }).code, "invalid_credentials");
  assert.equal(publicError(new CognitoError("UsernameExistsException", 400)).code, "cannot_sign_up");
  const challenge = (async () => new Response(JSON.stringify({ ChallengeName: "NEW_PASSWORD_REQUIRED" }))) as unknown as typeof fetch;
  await assert.rejects(signIn(cfg, "a@b.co", "pw", challenge), (e: unknown) => (e as { code?: string }).code === "unsupported_challenge");
});

test("sameOrigin (CSRF): requires an Origin equal to the host", () => {
  assert.equal(sameOrigin("https://app.example.com", "app.example.com"), true);
  assert.equal(sameOrigin("https://evil.example", "app.example.com"), false);
  assert.equal(sameOrigin(null, "app.example.com"), false);
  assert.equal(sameOrigin("not a url", "app.example.com"), false);
  assert.equal(sameOrigin("https://app.example.com", "internal:3000", "app.example.com"), true);
  // behind CloudFront -> Function URL: Host is the Lambda URL's, so the configured public host must be accepted, and only that one
  assert.equal(sameOrigin("https://d111.cloudfront.net", "abc.lambda-url.us-east-1.on.aws", null, "d111.cloudfront.net"), true);
  assert.equal(sameOrigin("https://evil.example", "abc.lambda-url.us-east-1.on.aws", null, "d111.cloudfront.net"), false);
  assert.equal(sameOrigin("https://d111.cloudfront.net", "abc.lambda-url.us-east-1.on.aws", null, undefined), false);
});
