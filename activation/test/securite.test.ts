// Audit de sécurité : chaque test reproduit une attaque ou une donnée hostile.
import { afterEach, describe, expect, it, vi } from "vitest";
import { createRequire } from "node:module";
import consoleWorker, { invalid } from "../src/console";
import activation from "../src/index";
import { loadFleet } from "../src/dashboard";
import { lit } from "../scripts/pilot.mjs";
import { demoGrafana, get, harness, NOW, ORIGIN, vector } from "./helpers";
import { fakeD1 } from "./fakeD1";

const { DatabaseSync } = createRequire(import.meta.url)("node:sqlite");
afterEach(() => vi.unstubAllGlobals());

const enc = new TextEncoder();
const part = (v: unknown) => Buffer.from(JSON.stringify(v)).toString("base64url");
// Objet JSON dont String() lève une TypeError : « toString » n'est pas une fonction.
const POISON = { toString: 1 };

describe("donnée hostile venue de Loki", () => {
  const hostile = (url: URL) => {
    if (url.host !== "loki.test") return demoGrafana(url);
    const line = { event: "support.report", description: POISON, app_version: POISON, last_error: POISON, engines: { micro: POISON } };
    return { data: { result: [{ stream: { school: "ipsa" }, values: [[String((NOW - 60) * 1e9), JSON.stringify(line)]] }] } };
  };
  it("un signalement piégé ne fait pas tomber Signalements ni la fiche de l'école", async () => {
    const h = await harness(hostile);
    for (const path of ["/signalements", "/ecoles/ipsa"]) {
      const r = await get(h, path);
      expect(r.status).toBe(200);
      expect(await r.text()).toContain("toString");
    }
  });
});

describe("nom d'école hostile dans Grafana", () => {
  it("« constructor » ne récupère pas les propriétés d'Object.prototype", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const q = new URL(url).searchParams.get("query") ?? "";
      return new Response(JSON.stringify(q.includes("timestamp(") ? vector([[{ school: "constructor" }, NOW]]) : vector([])));
    }));
    const r = await loadFleet({ PROM_URL: "https://prom.test", PROM_USER: "1", LOKI_URL: "https://loki.test", LOKI_USER: "2", GRAFANA_READ_TOKEN: "t" }, NOW);
    if (!r.ok) throw new Error(r.reason);
    const [s] = r.fleet.schools;
    expect(s.id).toBe("constructor");
    expect([s.members, s.envelopeAnnual, s.spendMonth, s.tokens30d]).toEqual([undefined, undefined, undefined, undefined]);
  });
});

describe("Access", () => {
  async function signed(h: Awaited<ReturnType<typeof harness>>, header: Record<string, unknown>, claims: Record<string, unknown>) {
    const input = `${part(header)}.${part(claims)}`;
    return `${input}.${Buffer.from(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", h.rsa.privateKey, enc.encode(input))).toString("base64url")}`;
  }
  const call = (h: Awaited<ReturnType<typeof harness>>, token: string, init: RequestInit = {}) =>
    consoleWorker.fetch(new Request(`${ORIGIN}/`, { ...init, headers: { "cf-access-jwt-assertion": token, ...(init.headers ?? {}) } }), h.env);

  it("refuse alg none, HS256, kid inconnu, exp absent, e-mail absent", async () => {
    const h = await harness();
    const good = { aud: "aud-1", exp: NOW + 600, email: "a@b.c" };
    expect((await call(h, `${part({ alg: "none", kid: "k1" })}.${part(good)}.x`)).status).toBe(403);
    expect((await call(h, await signed(h, { alg: "HS256", kid: "k1" }, good))).status).toBe(403);
    expect((await call(h, await signed(h, { alg: "RS256", kid: "autre" }, good))).status).toBe(403);
    expect((await call(h, await signed(h, { alg: "RS256", kid: "k1" }, { ...good, exp: undefined }))).status).toBe(403);
    expect((await call(h, await signed(h, { alg: "RS256", kid: "k1" }, { ...good, email: undefined }))).status).toBe(403);
    expect((await call(h, await signed(h, { alg: "RS256", kid: "k1" }, good))).status).toBe(200); // aud en chaîne accepté
  });

  it("une réponse de certs invalide n'est pas gardée en cache une heure", async () => {
    const h = await harness();
    let broken = true;
    vi.stubGlobal("fetch", vi.fn(async () =>
      broken ? new Response(JSON.stringify({ errors: ["indisponible"] }), { status: 503 }) : new Response(JSON.stringify({ keys: [h.jwk] }))));
    expect((await call(h, h.token)).status).toBe(403);
    broken = false;
    expect((await call(h, h.token)).status).toBe(200);
  });

  it("certs injoignables : 403, pas d'exception", async () => {
    const h = await harness();
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("réseau"); }));
    expect((await call(h, h.token)).status).toBe(403);
  });

  it("CSRF : tout POST sans Origin est refusé, même vers un chemin inconnu ; PUT, DELETE, HEAD → 404", async () => {
    const h = await harness();
    expect((await call(h, h.token, { method: "POST", body: "a=1", headers: { "content-type": "application/x-www-form-urlencoded" } })).status).toBe(403);
    for (const method of ["PUT", "DELETE", "PATCH", "HEAD"]) expect((await call(h, h.token, { method })).status).toBe(404);
  });
});

describe("licences", () => {
  const base = { organization: "IPSA", tier: "x", kind: "pilot", starts: "2026-01-01", ends: "2026-12-31" };
  it("refuse une enveloppe d'IA infinie (sérialisée null dans la licence)", () => {
    expect(invalid({ ...base, ai_envelope_eur: Infinity })).not.toBeNull();
    expect(invalid({ ...base, ai_envelope_eur: 12 })).toBeNull();
  });
});

describe("API d'activation", () => {
  it("refuse une date d'expiration hors du calendrier, qui ferait planter la page Licences", async () => {
    const env = { DB: fakeD1(), ADMIN_TOKEN: "admin" };
    const r = await activation.fetch(new Request("https://a.test/codes", {
      method: "POST", headers: { authorization: "Bearer admin" },
      body: JSON.stringify({ code_hash: "a".repeat(64), licence: "x.e30.y", school: "S", expires_at: 1e300 }),
    }), env);
    expect(r.status).toBe(400);
  });
});

describe("scripts/pilot.mjs lit()", () => {
  it("rend tout texte tel quel en SQL : apostrophes, antislashs, retours, octet nul, mots de transaction", () => {
    const db = new DatabaseSync(":memory:");
    const text = "l'école \\ \"x\" ;\n-- /* $$ */ BEGIN TRANSACTION; COMMIT; \0 fin é🙂";
    expect(lit(text)).not.toMatch(/BEGIN TRANSACTION|COMMIT;|\0/);
    expect((db.prepare(`SELECT ${lit(text)} AS v`).get() as { v: string }).v).toBe(text);
    expect([lit(null), lit(undefined), lit(3)]).toEqual(["NULL", "NULL", "3"]);
  });
});
