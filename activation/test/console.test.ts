import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import consoleWorker, { invalid, newCode, signLicence, type ConsoleEnv } from "../src/console";
import { sha256Hex } from "../src/index";
import { fakeD1 } from "./fakeD1";

const NOW = Math.floor(Date.now() / 1000);
const ORIGIN = "https://console.test";
const enc = new TextEncoder();
const b64url = (bytes: Uint8Array) =>
  Buffer.from(bytes).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const part = (value: unknown) => b64url(enc.encode(JSON.stringify(value)));

let env: ConsoleEnv;
let rsa: CryptoKeyPair;
let signingPublic: CryptoKey;

async function accessToken(claims: Record<string, unknown>, key = rsa.privateKey) {
  const input = `${part({ alg: "RS256", kid: "k1" })}.${part(claims)}`;
  const signature = new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, enc.encode(input)));
  return `${input}.${b64url(signature)}`;
}
const goodToken = () => accessToken({ aud: ["aud-1"], exp: NOW + 600, email: "sash@example.org" });

async function call(path: string, init: RequestInit & { token?: string } = {}) {
  const headers = new Headers(init.headers);
  if (init.token) headers.set("cf-access-jwt-assertion", init.token);
  return consoleWorker.fetch(new Request(`${ORIGIN}${path}`, { ...init, headers }), env);
}
const form = (fields: Record<string, string>) => new URLSearchParams(fields).toString();
const post = async (fields: Record<string, string>, origin = ORIGIN) =>
  call("/codes", {
    method: "POST", token: await goodToken(), body: form(fields),
    headers: { "content-type": "application/x-www-form-urlencoded", origin },
  });
const valid = { organization: "IPSA", tier: "151-250", kind: "subscription", starts: "2026-09-01", ends: "2027-08-31", code_days: "30" };

beforeEach(async () => {
  rsa = (await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["sign", "verify"],
  )) as CryptoKeyPair;
  const jwk = { ...(await crypto.subtle.exportKey("jwk", rsa.publicKey)), kid: "k1" };
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ keys: [jwk] }))));
  const ed = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
  signingPublic = ed.publicKey;
  const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", ed.privateKey)).toString("base64");
  env = {
    DB: fakeD1(),
    SIGNING_KEY: `-----BEGIN PRIVATE KEY-----\n${pkcs8}\n-----END PRIVATE KEY-----\n`,
    // Domaine propre à chaque test : le cache des clés d'Access ne déborde pas.
    ACCESS_TEAM_DOMAIN: `team-${crypto.randomUUID()}.cloudflareaccess.com`,
    ACCESS_AUD: "aud-1",
    GRAFANA_URL: "https://grafana.test",
  };
});
afterEach(() => vi.unstubAllGlobals());

describe("accès", () => {
  it("refuse sans jeton d'Access", async () => {
    expect((await call("/")).status).toBe(403);
  });
  it("refuse un jeton signé par une autre clé, d'une autre audience ou expiré", async () => {
    const other = (await crypto.subtle.generateKey(
      { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
      true, ["sign"],
    )) as CryptoKeyPair;
    const claims = { aud: ["aud-1"], exp: NOW + 600, email: "x@y.z" };
    expect((await call("/", { token: await accessToken(claims, other.privateKey) })).status).toBe(403);
    expect((await call("/", { token: await accessToken({ ...claims, aud: ["autre"] }) })).status).toBe(403);
    expect((await call("/", { token: await accessToken({ ...claims, exp: NOW - 1 }) })).status).toBe(403);
  });
  it("refuse tout si Access n'est pas réglé", async () => {
    env.ACCESS_AUD = "";
    expect((await call("/", { token: await goodToken() })).status).toBe(403);
  });
  it("affiche la page avec un jeton valable", async () => {
    const response = await call("/", { token: await goodToken() });
    expect(response.status).toBe(200);
    expect(await response.text()).toContain("sash@example.org");
  });
});

describe("création d'un code", () => {
  it("stocke une licence signée et seulement le condensé du code", async () => {
    const response = await post(valid);
    const html = await response.text();
    const code = html.match(/NOVA-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}/)![0];
    const row = (await env.DB.prepare("SELECT * FROM codes").first()) as Record<string, string | number>;
    expect(row.code_hash).toBe(await sha256Hex(code.replace(/-/g, "")));
    expect(row.school).toBe("IPSA");
    expect(row.expires_at).toBeGreaterThanOrEqual(NOW + 30 * 86400);
    const [head, body, signature] = String(row.licence).split(".");
    const ok = await crypto.subtle.verify("Ed25519", signingPublic, Buffer.from(signature, "base64url"), enc.encode(`${head}.${body}`));
    expect(ok).toBe(true);
    expect(JSON.parse(Buffer.from(body, "base64url").toString())).toEqual({
      organization: "IPSA", tier: "151-250", kind: "subscription", starts: "2026-09-01", ends: "2027-08-31",
    });
    expect(html).not.toContain(String(row.licence));
  });
  it("refuse un formulaire venu d'un autre site", async () => {
    expect((await post(valid, "https://ailleurs.test")).status).toBe(403);
    expect(await env.DB.prepare("SELECT * FROM codes").first()).toBeNull();
  });
  it("refuse des dates à l'envers sans rien stocker", async () => {
    const html = await (await post({ ...valid, ends: "2026-01-01" })).text();
    expect(html).toContain("La fin est avant le début.");
    expect(await env.DB.prepare("SELECT * FROM codes").first()).toBeNull();
  });
  it("échappe le nom de l'établissement", async () => {
    const html = await (await post({ ...valid, organization: "<script>x</script>" })).text();
    expect(html).not.toContain("<script>x");
  });
});

describe("règles", () => {
  it("valide comme licence.issue()", () => {
    const base = { organization: "A", tier: "t", kind: "pilot", starts: "2026-01-01", ends: "2026-02-01" };
    expect(invalid(base)).toBeNull();
    expect(invalid({ ...base, kind: "autre" })).not.toBeNull();
    expect(invalid({ ...base, starts: "2026-13-40" })).not.toBeNull();
    expect(invalid({ ...base, ai_envelope_eur: -1 })).not.toBeNull();
  });
  it("tire des codes au bon format, sans caractères ambigus", () => {
    for (let i = 0; i < 50; i++) expect(newCode()).toMatch(/^NOVA-[2-9A-HJ-NP-Z]{4}-[2-9A-HJ-NP-Z]{4}$/);
  });
  it("signe un jeton EdDSA à trois parties", async () => {
    const token = await signLicence(env.SIGNING_KEY, { organization: "A", tier: "t", kind: "pilot", starts: "2026-01-01", ends: "2026-02-01" });
    expect(JSON.parse(Buffer.from(token.split(".")[0], "base64url").toString())).toEqual({ alg: "EdDSA", typ: "JWT" });
  });
});

describe("pilotage de Claude", () => {
  const send = async (path: string, fields: Record<string, string>, origin = ORIGIN) =>
    call(path, { method: "POST", token: await goodToken(), body: form(fields), headers: { "content-type": "application/x-www-form-urlencoded", origin } });
  const pageText = async (vue = "") => (await call(`/claude${vue}`, { token: await goodToken() })).text();

  it("met en pause puis relance, avec l'auteur", async () => {
    expect((await send("/pilot/pause", { paused: "1" })).status).toBe(303);
    expect(await pageText()).toContain("En pause");
    expect(await env.DB.prepare("SELECT value, by FROM pilot_state").first()).toEqual({ value: "1", by: "sash@example.org" });
    await send("/pilot/pause", { paused: "0" });
    expect(await pageText()).toContain("Mettre Claude en pause");
  });
  it("enregistre une consigne, échappée, et refuse une autre origine", async () => {
    expect((await send("/pilot/message", { body: "<img src=x>" }, "https://ailleurs.test")).status).toBe(403);
    expect(await env.DB.prepare("SELECT * FROM pilot_messages").first()).toBeNull();
    await send("/pilot/message", { body: "<img src=x>" });
    const html = await pageText();
    expect(html).not.toContain("<img src=x>");
    expect(html).toContain("Pas encore lu");
  });
  it("retient puis relâche une PR, refuse un dépôt invalide", async () => {
    await env.DB.prepare("INSERT INTO pilot_prs VALUES ('nova-server', 12, 'feat: x', 'vert', 'https://github.com/x', ?)").bind(NOW).run();
    await send("/pilot/hold", { repo: "nova-server", number: "12", hold: "1", reason: "attend Sash" });
    expect(await pageText("?vue=pr")).toContain("Retenue : attend Sash");
    await send("/pilot/hold", { repo: "nova-server", number: "12", hold: "0" });
    expect(await pageText("?vue=pr")).toContain("Autorisée si verte");
    expect((await send("/pilot/hold", { repo: "../x", number: "1", hold: "1" })).status).toBe(400);
  });
  it("répond une seule fois à une question, et montre le rapport détaillé du journal", async () => {
    await env.DB.prepare("INSERT INTO pilot_questions (at, title) VALUES (?, 'Prix ?')").bind(NOW).run();
    await env.DB.prepare("INSERT INTO pilot_journal (at, kind, result, title, details) VALUES (?, 'bug', 'ok', 'Bulle', 'journal: <err>')").bind(NOW).run();
    await send("/pilot/answer", { id: "1", answer: "12 €" });
    await send("/pilot/answer", { id: "1", answer: "autre" });
    expect(await env.DB.prepare("SELECT answer FROM pilot_questions").first()).toEqual({ answer: "12 €" });
    const html = await pageText("?vue=journal");
    expect(html).toContain("Rapport détaillé");
    expect(html).toContain("journal: &#60;err&#62;");
  });
});

describe("en-têtes", () => {
  it("garde l'origine des formulaires (Chrome envoie Origin: null avec no-referrer)", async () => {
    const response = await call("/", { token: await goodToken() });
    expect(response.headers.get("referrer-policy")).toBe("same-origin");
  });
});
