// Outils communs aux tests de la console : environnement, jeton d'Access signé, faux Grafana.
import { vi } from "vitest";
import consoleWorker, { type ConsoleEnv } from "../src/console";
import { fakeD1 } from "./fakeD1";

export const NOW = Math.floor(Date.now() / 1000);
export const ORIGIN = "https://console.test";
const enc = new TextEncoder();
const b64url = (bytes: Uint8Array) => Buffer.from(bytes).toString("base64url");
const part = (value: unknown) => b64url(enc.encode(JSON.stringify(value)));

export interface Harness { env: ConsoleEnv; token: string; rsa: CryptoKeyPair; jwk: JsonWebKey }

/** Environnement complet + jeton d'Access valable pour sash@example.org. `grafana` répond aux requêtes PromQL/LogQL. */
export async function harness(grafana?: (url: URL) => unknown | null): Promise<Harness> {
  const rsa = (await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true, ["sign", "verify"],
  )) as CryptoKeyPair;
  const jwk = { ...(await crypto.subtle.exportKey("jwk", rsa.publicKey)), kid: "k1" };
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = new URL(input);
    if (url.pathname.endsWith("/cdn-cgi/access/certs")) return new Response(JSON.stringify({ keys: [jwk] }));
    const body = grafana ? grafana(url) : null;
    return body === null ? new Response("", { status: 503 }) : new Response(JSON.stringify(body));
  }));
  const ed = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"])) as CryptoKeyPair;
  const pkcs8 = Buffer.from(await crypto.subtle.exportKey("pkcs8", ed.privateKey)).toString("base64");
  const env: ConsoleEnv = {
    DB: fakeD1(),
    SIGNING_KEY: `-----BEGIN PRIVATE KEY-----\n${pkcs8}\n-----END PRIVATE KEY-----\n`,
    ACCESS_TEAM_DOMAIN: `team-${crypto.randomUUID()}.cloudflareaccess.com`,
    ACCESS_AUD: "aud-1",
    GRAFANA_URL: "https://grafana.test",
    PROM_URL: "https://prom.test/api/prom", PROM_USER: "1", LOKI_URL: "https://loki.test", LOKI_USER: "2", GRAFANA_READ_TOKEN: "t",
  };
  const input = `${part({ alg: "RS256", kid: "k1" })}.${part({ aud: ["aud-1"], exp: NOW + 600, email: "sash@example.org" })}`;
  const token = `${input}.${b64url(new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", rsa.privateKey, enc.encode(input))))}`;
  return { env, token, rsa, jwk };
}

/** GET authentifié ; renvoie la réponse. */
export const get = (h: Harness, path: string) =>
  consoleWorker.fetch(new Request(`${ORIGIN}${path}`, { headers: { "cf-access-jwt-assertion": h.token } }), h.env);

/** POST de formulaire authentifié, depuis la console elle-même. */
export const post = (h: Harness, path: string, fields: Record<string, string>, origin = ORIGIN) =>
  consoleWorker.fetch(new Request(`${ORIGIN}${path}`, {
    method: "POST", body: new URLSearchParams(fields).toString(),
    headers: { "cf-access-jwt-assertion": h.token, "content-type": "application/x-www-form-urlencoded", origin },
  }), h.env);

/** Vecteur PromQL instantané. */
export const vector = (rows: [Record<string, string>, number][]) =>
  ({ data: { result: rows.map(([metric, v]) => ({ metric, value: [NOW, String(v)] })) } });

/** Faux Grafana Cloud : deux écoles (ipsa en ligne, lycee-test muette depuis 2 h), un signalement. */
export function demoGrafana(url: URL): unknown {
  const q = url.searchParams.get("query") ?? "";
  const a = { school: "ipsa" }, b = { school: "lycee-test" };
  if (url.host === "loki.test") {
    const line = { event: "support.report", app_version: "1.0.82", engines: { microphone: "ok", transcription_model: "warning" }, last_error: "", description: "Le micro coupe" };
    return { data: { result: [{ stream: { school: "ipsa" }, values: [[String((NOW - 3600) * 1e9), JSON.stringify(line)]] }] } };
  }
  if (url.pathname.endsWith("query_range")) {
    return { data: { result: [a, b].map((m) => ({ metric: m, values: Array.from({ length: 14 }, (_, i) => [NOW - (13 - i) * 86400, String(q.includes("histogram") ? 1.2 : 100 + i)]) })) } };
  }
  if (q.includes("timestamp(nova_uptime_seconds)")) return vector([[a, NOW - 30], [b, NOW - 7200]]);
  if (q.includes("nova_server_info")) return vector([[{ ...a, server_version: "0.2.0" }, 1], [{ ...b, server_version: "0.1.9" }, 1]]);
  if (q.includes("nova_app_versions")) return vector([[{ ...a, version: "1.0.82" }, 31], [{ ...b, version: "1.0.80" }, 12]]);
  if (q.includes("nova_members")) return vector([[a, 212], [b, 40]]);
  if (q.includes("nova_active_users")) return vector([[a, 148], [b, 22]]);
  if (q.includes("nova_licence_info")) return vector([[{ ...a, status: "valid", tier: "151-250" }, 1], [{ ...b, status: "warning", tier: "pilot" }, 1]]);
  if (q.includes("nova_licence_days_left")) return vector([[a, 289], [b, 12]]);
  if (q.includes("nova_ai_spend_eur_month")) return vector([[a, 90], [b, 9.2]]);
  if (q.includes("nova_ai_envelope_eur")) return vector([[a, 1200]]);
  if (q.includes("nova_ai_tokens_total")) return vector([[a, 4210000], [b, 512000]]);
  if (q.includes("nova_events_total")) return vector([[a, 1834], [b, 96]]);
  return vector([]);
}
