import { beforeEach, describe, expect, it } from "vitest";
import worker, { sha256Hex, type Env } from "../src/index";
import { fakeD1 } from "./fakeD1";

const NOW = Math.floor(Date.now() / 1000);
const call = (env: Env, path: string, body: unknown, headers: Record<string, string> = {}) =>
  worker.fetch(new Request(`https://x${path}`, {
    method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body),
  }), env);
const admin = { authorization: "Bearer secret-admin" };

let env: Env;
async function addCode(code: string, expires = NOW + 3600) {
  return call(env, "/codes", { code_hash: await sha256Hex(code), licence: "LICENCE.SIGNEE", school: "IPSA", expires_at: expires }, admin);
}

beforeEach(() => { env = { DB: fakeD1(), ADMIN_TOKEN: "secret-admin" }; });

describe("administration", () => {
  it("refuse sans jeton ou avec un mauvais jeton", async () => {
    const body = { code_hash: "a".repeat(64), licence: "L", school: "S", expires_at: NOW + 60 };
    expect((await call(env, "/codes", body)).status).toBe(401);
    expect((await call(env, "/codes", body, { authorization: "Bearer autre" })).status).toBe(401);
  });
  it("refuse tout si ADMIN_TOKEN n'est pas défini", async () => {
    env.ADMIN_TOKEN = "";
    expect((await call(env, "/codes", {}, { authorization: "Bearer " })).status).toBe(401);
  });
});

describe("échange du code", () => {
  it("rend la licence, tolère la casse et les tirets", async () => {
    expect((await addCode("NOVA7K2P91XQ")).status).toBe(201);
    const response = await call(env, "/redeem", { code: "nova-7k2p-91xq" });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ licence: "LICENCE.SIGNEE" });
  });
  it("refuse un code réutilisé", async () => {
    await addCode("NOVA7K2P91XQ");
    expect((await call(env, "/redeem", { code: "NOVA-7K2P-91XQ" })).status).toBe(200);
    expect((await call(env, "/redeem", { code: "NOVA-7K2P-91XQ" })).status).toBe(403);
  });
  it("refuse un code expiré, sans le consommer", async () => {
    await addCode("NOVA7K2P91XQ", NOW + 1);
    await env.DB.prepare("UPDATE codes SET expires_at = ?").bind(NOW - 10).run();
    expect((await call(env, "/redeem", { code: "NOVA-7K2P-91XQ" })).status).toBe(403);
  });
  it("refuse un code inconnu avec la même réponse", async () => {
    const response = await call(env, "/redeem", { code: "NOVA-AAAA-BBBB" });
    expect([response.status, await response.json()]).toEqual([403, { error: "invalid_code" }]);
  });
  it("limite le débit par adresse", async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 12; i++) {
      statuses.push((await call(env, "/redeem", { code: `NOVA-0000-${i}` }, { "cf-connecting-ip": "1.2.3.4" })).status);
    }
    expect(statuses.slice(0, 10).every((s) => s === 403)).toBe(true);
    expect(statuses.slice(10)).toEqual([429, 429]);
  });
  it("ne stocke jamais le code en clair", async () => {
    await addCode("NOVA7K2P91XQ");
    const row = (await env.DB.prepare("SELECT code_hash FROM codes").first()) as { code_hash: string };
    expect(row.code_hash).toBe(await sha256Hex("NOVA7K2P91XQ"));
  });
});
