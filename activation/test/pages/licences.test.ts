import { afterEach, describe, expect, it, vi } from "vitest";
import { csvCell, renewal } from "../../src/pages/licences";
import { reportKey } from "../../src/pages/signalements";
import { sha256Hex } from "../../src/index";
import { demoGrafana, get, harness, NOW, post, type Harness } from "../helpers";

afterEach(() => vi.unstubAllGlobals());

const DAY = 86400;
const b64 = (v: unknown) => Buffer.from(JSON.stringify(v)).toString("base64url");
const token = (claims: Record<string, unknown>) => `${b64({ alg: "EdDSA" })}.${b64(claims)}.sig`;

async function addCode(h: Harness, school: string, opts: { expires?: number; used?: number | null; ends?: string; envelope?: number } = {}) {
  const hash = await sha256Hex(`code-${school}`);
  const claims = { organization: school, tier: "151-250", kind: "subscription", starts: "2026-09-01", ends: opts.ends ?? "2027-08-31", ...(opts.envelope !== undefined ? { ai_envelope_eur: opts.envelope } : {}) };
  await h.env.DB.prepare("INSERT INTO codes (code_hash, licence, school, expires_at, used_at) VALUES (?, ?, ?, ?, ?)")
    .bind(hash, token(claims), school, opts.expires ?? NOW + 10 * DAY, opts.used ?? null).run();
  return hash;
}
const seed = async (h: Harness) => {
  await addCode(h, "Attente");
  await addCode(h, "Active", { used: NOW - DAY });
  await addCode(h, "Perime", { expires: NOW - DAY });
};
const text = async (h: Harness, path: string) => (await get(h, path)).text();

describe("licences : filtres", () => {
  it("filtre par état", async () => {
    const h = await harness();
    await seed(h);
    const wait = await text(h, "/licences?etat=attente");
    expect(wait).toContain("<strong>Attente</strong>");
    expect(wait).not.toContain("<strong>Active</strong>");
    const act = await text(h, "/licences?etat=actives");
    expect(act).toContain("<strong>Active</strong>");
    expect(act).not.toContain("<strong>Perime</strong>");
    const exp = await text(h, "/licences?etat=expires");
    expect(exp).toContain("<strong>Perime</strong>");
    expect(exp).not.toContain("<strong>Attente</strong>");
  });
  it("recherche insensible à la casse, combinée à l'état", async () => {
    const h = await harness();
    await seed(h);
    await addCode(h, "Attente Bis", { used: NOW - DAY });
    const html = await text(h, "/licences?q=ATTENTE");
    expect(html).toContain("<strong>Attente</strong>");
    expect(html).toContain("<strong>Attente Bis</strong>");
    expect(html).not.toContain("<strong>Perime</strong>");
    const both = await text(h, "/licences?q=attente&etat=actives");
    expect(both).toContain("<strong>Attente Bis</strong>");
    expect(both).not.toContain("<strong>Attente</strong>");
  });
});

describe("licences : renouvellement", () => {
  it("renewal : lendemain de la fin, un an moins un jour, enveloppe reprise", () => {
    expect(renewal({ organization: "A", tier: "t", kind: "pilot", ends: "2027-08-31", ai_envelope_eur: 1200 }))
      .toEqual({ organization: "A", tier: "t", kind: "pilot", starts: "2027-09-01", ends: "2028-08-31", ai_envelope_eur: 1200 });
    expect(renewal({ organization: "A", tier: "t", kind: "pilot", ends: "2027-08-31" })).not.toHaveProperty("ai_envelope_eur");
  });
  it("renewal : 29 février", () => {
    expect(renewal({ ends: "2028-02-29" })).toMatchObject({ starts: "2028-03-01", ends: "2029-02-28" });
    expect(renewal({ ends: "2028-02-28" })).toMatchObject({ starts: "2028-02-29", ends: "2029-02-28" });
    expect(renewal({ ends: "n'importe quoi" })).toBeNull();
  });
  it("?renouveler pré-remplit le formulaire", async () => {
    const h = await harness();
    const hash = await addCode(h, "Lycée Hugo", { envelope: 900 });
    const html = await text(h, `/licences?renouveler=${hash}`);
    expect(html).toContain('id="nouveau"');
    expect(html).toContain('value="Lycée Hugo"');
    expect(html).toContain('value="2027-09-01"');
    expect(html).toContain('value="2028-08-31"');
    expect(html).toContain('value="900"');
    expect(html).toContain("Renouvellement de Lycée Hugo : vérifiez puis créez le code.");
  });
});

describe("licences : révocation", () => {
  it("expire un code en attente et écrit l'audit", async () => {
    const h = await harness();
    const hash = await addCode(h, "Attente");
    const res = await post(h, "/codes/revoke", { code_hash: hash });
    expect(res.status).toBe(303);
    const row = await h.env.DB.prepare("SELECT expires_at, used_at FROM codes").first<{ expires_at: number; used_at: number | null }>();
    expect(row!.expires_at).toBeLessThanOrEqual(Math.floor(Date.now() / 1000));
    expect(row!.used_at).toBeNull();
    expect(await h.env.DB.prepare("SELECT action, detail FROM console_audit").first()).toEqual({ action: "Code révoqué", detail: "Attente" });
  });
  it("ne touche pas un code déjà utilisé", async () => {
    const h = await harness();
    const hash = await addCode(h, "Active", { used: NOW - DAY });
    const before = await h.env.DB.prepare("SELECT * FROM codes").first();
    expect((await post(h, "/codes/revoke", { code_hash: hash })).status).toBe(303);
    expect(await h.env.DB.prepare("SELECT * FROM codes").first()).toEqual(before);
    expect(await h.env.DB.prepare("SELECT action FROM console_audit").first()).toEqual({ action: "Révocation sans effet" });
  });
  it("le bouton n'existe que pour un code en attente", async () => {
    const h = await harness();
    await addCode(h, "Active", { used: NOW - DAY });
    expect(await text(h, "/licences")).not.toContain("/codes/revoke");
    await addCode(h, "Attente");
    expect(await text(h, "/licences")).toContain("/codes/revoke");
  });
  it("refuse une origine étrangère et un identifiant invalide", async () => {
    const h = await harness();
    const hash = await addCode(h, "Attente");
    expect((await post(h, "/codes/revoke", { code_hash: hash }, "https://ailleurs.test")).status).toBe(403);
    expect((await post(h, "/codes/revoke", { code_hash: "x" })).status).toBe(400);
  });
});

describe("licences : export CSV", () => {
  it("une ligne par code, BOM, séparateur point-virgule", async () => {
    const h = await harness();
    await seed(h);
    const res = await get(h, "/licences.csv");
    expect(res.headers.get("content-type")).toBe("text/csv; charset=utf-8");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="codes-nova.csv"');
    const raw = new Uint8Array(await res.clone().arrayBuffer());
    expect([...raw.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    const body = await res.text();
    const lines = body.trim().split("\r\n");
    expect(lines[0].replace(/^﻿/, "")).toBe("etablissement;offre;type;debut;fin;enveloppe_eur;etat;code_valable_jusqu_au;active_le");
    expect(lines).toHaveLength(4);
    expect(body).toContain("en attente");
  });
  it("neutralise une formule dans un nom", async () => {
    const h = await harness();
    await addCode(h, '=HYPERLINK("http://x";"y")');
    const body = await text(h, "/licences.csv");
    expect(body).toContain(`"'=HYPERLINK(""http://x"";""y"")"`);
  });
  it("csvCell", () => {
    expect(csvCell("=1+1")).toBe("'=1+1");
    expect(csvCell("+1")).toBe("'+1");
    expect(csvCell("-1")).toBe("'-1");
    expect(csvCell("@a")).toBe("'@a");
    expect(csvCell("\tx")).toBe("'\tx");
    expect(csvCell("a;b")).toBe('"a;b"');
    expect(csvCell('a"b')).toBe('"a""b"');
    expect(csvCell(undefined)).toBe("");
    expect(csvCell(1200)).toBe("1200");
  });
});

describe("signalements", () => {
  const key = reportKey({ at: NOW - 3600, school: "ipsa", line: {} });
  it("reportKey", () => expect(reportKey({ at: 1.5, school: "a:b", line: {} })).toBe("a:b:1500"));

  it("montre le signalement à traiter avec ses moteurs", async () => {
    const h = await harness(demoGrafana);
    const html = await text(h, "/signalements");
    expect(html).toContain("Le micro coupe");
    expect(html).toContain("Nova 1.0.82");
    expect(html).toContain("transcription_model : warning");
    expect(html).not.toContain("microphone : ok");
  });
  it("traite, puis déplace dans Traités ; rouvre", async () => {
    const h = await harness(demoGrafana);
    const res = await post(h, "/signalements/statut", { report_key: key, statut: "traite", note: "Pilote mis à jour", vue: "tous" });
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/signalements?vue=tous");
    expect(await text(h, "/signalements?vue=traites")).toContain("Traité par sash@example.org");
    expect(await text(h, "/signalements?vue=traites")).toContain("Pilote mis à jour");
    expect(await text(h, "/signalements")).not.toContain("Le micro coupe");
    expect(await h.env.DB.prepare("SELECT action, detail FROM console_audit").first()).toEqual({ action: "Signalement traité", detail: key });
    await post(h, "/signalements/statut", { report_key: key, statut: "rouvrir" });
    expect(await text(h, "/signalements")).toContain("Le micro coupe");
    expect(await h.env.DB.prepare("SELECT * FROM report_status").first()).toBeNull();
  });
  it("ignorer s'affiche « Ignoré »", async () => {
    const h = await harness(demoGrafana);
    await post(h, "/signalements/statut", { report_key: key, statut: "ignore" });
    expect(await text(h, "/signalements?vue=traites")).toContain("Ignoré par sash@example.org");
  });
  it("refuse une clé invalide, un statut inconnu ou une origine étrangère", async () => {
    const h = await harness(demoGrafana);
    expect((await post(h, "/signalements/statut", { report_key: "pas de clé", statut: "traite" })).status).toBe(400);
    expect((await post(h, "/signalements/statut", { report_key: `a:${"1".repeat(250)}`, statut: "traite" })).status).toBe(400);
    expect((await post(h, "/signalements/statut", { report_key: key, statut: "x" })).status).toBe(400);
    expect((await post(h, "/signalements/statut", { report_key: key, statut: "traite" }, "https://ailleurs.test")).status).toBe(403);
    expect(await h.env.DB.prepare("SELECT * FROM report_status").first()).toBeNull();
  });
  it("échappe la description", async () => {
    const h = await harness((url) => {
      if (url.host !== "loki.test") return demoGrafana(url);
      const line = { event: "support.report", description: "<script>alert(1)</script>" };
      return { data: { result: [{ stream: { school: "ipsa" }, values: [[String((NOW - 60) * 1e9), JSON.stringify(line)]] }] } };
    });
    const html = await text(h, "/signalements");
    expect(html).not.toContain("<script>alert");
    expect(html).toContain("&#60;script&#62;");
  });
  it("explique l'absence de journaux et la panne de Grafana", async () => {
    const h = await harness((url) => (url.host === "loki.test" ? null : demoGrafana(url)));
    expect(await text(h, "/signalements")).toContain("logs:read");
    const g = await harness(() => null);
    expect(await text(g, "/signalements")).toContain("Grafana Cloud ne répond pas");
  });
});

describe("création de code", () => {
  it("recharger la page après « Créer le code » ne crée pas de second code", async () => {
    const h = await harness();
    const nonce = /name="nonce" value="([^"]+)"/.exec(await text(h, "/licences"))![1];
    const form = { nonce, organization: "IPSA", tier: "151-250", kind: "pilot", starts: "2026-11-13", ends: "2026-12-13", code_days: "7" };
    expect(await (await post(h, "/codes", form)).text()).toContain('class="big"');
    const again = await (await post(h, "/codes", form)).text();
    expect(again).not.toContain('class="big"');
    expect(again).toContain("déjà été créé");
    expect(await h.env.DB.prepare("SELECT COUNT(*) AS n FROM codes").first()).toEqual({ n: 1 });
  });
});

describe("licences : au-delà de 500 codes", () => {
  it("le CSV et les compteurs couvrent tous les codes, la liste annonce les codes masqués", async () => {
    const h = await harness();
    for (let i = 0; i < 520; i++) await addCode(h, `Ecole${i}`);
    const csv = await text(h, "/licences.csv");
    expect(csv.trim().split("\r\n")).toHaveLength(521); // en-tête + 520
    const page = await text(h, "/licences");
    expect(page).toContain(">520<");
    expect(page).toContain("20 codes de plus ne sont pas affichés");
  });
});

describe("création de code : durée de validité", () => {
  const days = async (code_days: string) => {
    const h = await harness();
    await post(h, "/codes", { organization: "IPSA", tier: "151-250", kind: "pilot", starts: "2026-11-13", ends: "2026-12-13", code_days });
    const row = await h.env.DB.prepare("SELECT expires_at FROM codes").first<{ expires_at: number }>();
    return Math.round((row!.expires_at - Math.floor(Date.now() / 1000)) / DAY);
  };
  it("borne 0, négatif et trop grand à 1..90 ; vide ou illisible → 30", async () => {
    expect(await days("0")).toBe(1);
    expect(await days("-5")).toBe(1);
    expect(await days("500")).toBe(90);
    expect(await days("")).toBe(30);
    expect(await days("abc")).toBe(30);
    expect(await days("7")).toBe(7);
  });
});
