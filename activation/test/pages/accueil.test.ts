import { afterEach, describe, expect, it, vi } from "vitest";
import { todo, untreated, type TodoInput } from "../../src/pages/accueil";
import type { Fleet, SchoolData } from "../../src/dashboard";
import { demoGrafana, get, harness, NOW } from "../helpers";

afterEach(() => vi.unstubAllGlobals());

const school = (o: Partial<SchoolData> = {}): SchoolData => ({
  id: "ecole-a", lastSeen: NOW - 10, online: true, apps: [], dictSeries: [], latencySeries: [], ...o,
});
const input = (schools: SchoolData[] = [], o: Partial<TodoInput> = {}, reports: Fleet["reports"] = []): TodoInput => ({
  fleet: { ok: true, fleet: { schools, reports } }, now: NOW, openQuestions: 0, paused: false, pending: [], treated: new Set(), ...o,
});

describe("todo()", () => {
  it("liste vide quand tout va bien", () => expect(todo(input([school()]))).toEqual([]));

  it("école hors ligne", () => {
    const [t] = todo(input([school({ id: "a b", online: false, lastSeen: NOW - 7200 })]));
    expect(t).toMatchObject({ tone: "bad", title: "a b ne répond plus", detail: "dernier envoi il y a 2 h", href: "/ecoles/a%20b" });
  });

  it("licences : échue, invalide, grâce = bad ; warning et ≤ 30 j = warn", () => {
    const tone = (status: string, daysLeft?: number) => todo(input([school({ licence: { status, tier: "x", daysLeft } })]))[0]?.tone;
    expect(tone("expired")).toBe("bad");
    expect(tone("invalid")).toBe("bad");
    expect(tone("grace")).toBe("bad");
    expect(tone("warning")).toBe("warn");
    expect(tone("valid", 30)).toBe("warn");
    expect(tone("valid", 31)).toBeUndefined();
    expect(todo(input([school({ licence: { status: "valid", tier: "x", daysLeft: 12 } })]))[0].title).toBe("Licence de ecole-a : 12 j restants");
    expect(todo(input([school({ licence: { status: "grace", tier: "x" } })]))[0].title).toContain("en délai de grâce");
  });

  it("enveloppe d'IA : > 0,8 warn, > 1 bad, href /ia", () => {
    const at = (spend: number) => todo(input([school({ envelopeAnnual: 1200, spendMonth: spend })]))[0];
    expect(at(80)).toBeUndefined();
    expect(at(81)).toMatchObject({ tone: "warn", href: "/ia" });
    expect(at(101)).toMatchObject({ tone: "bad", href: "/ia" });
  });

  it("signalements récents non traités : un seul élément", () => {
    const r = (at: number) => ({ at, school: "ecole-a", line: {} });
    const reports = [r(NOW - 100), r(NOW - 200), r(NOW - 300), r(NOW - 8 * 86400), r(NOW - 31 * 86400)];
    const treated = new Set([`ecole-a:${Math.round((NOW - 300) * 1000)}`]);
    const items = todo(input([school()], { treated }, reports));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ tone: "warn", title: "3 signalements à traiter", href: "/signalements" });
  });

  it("questions ouvertes et pause", () => {
    expect(todo(input([], { openQuestions: 1 }))[0]).toMatchObject({ tone: "accent", title: "Claude attend 1 réponse", href: "/claude" });
    expect(todo(input([], { openQuestions: 3 }))[0].title).toBe("Claude attend 3 réponses");
    expect(todo(input([], { paused: true }))[0]).toMatchObject({ tone: "neutral", title: "Claude est en pause", href: "/claude" });
  });

  it("code d'activation qui expire dans moins de 7 jours", () => {
    const pending = [{ school: "Lycée X", expires_at: NOW + 86400 }, { school: "Lycée Y", expires_at: NOW + 20 * 86400 }];
    const items = todo(input([], { pending }));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ tone: "warn", href: "/licences" });
    expect(items[0].title).toMatch(/^Code pour Lycée X : expire le /);
  });

  it("postes sur une ancienne version (comparaison numérique)", () => {
    const a = school({ id: "a", apps: [{ version: "1.0.9", count: 2 }, { version: "1.0.10", count: 5 }] });
    const b = school({ id: "b", apps: [{ version: "1.0.10", count: 4 }] });
    const items = todo(input([a, b]));
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ tone: "neutral", title: "a : 2 postes sur une ancienne version", href: "/ecoles/a" });
  });

  it("Grafana injoignable : bad, et les autres règles continuent", () => {
    const items = todo({ ...input([], { openQuestions: 2 }), fleet: { ok: false, reason: "token" } });
    expect(items.map((t) => t.title)).toEqual(["Grafana Cloud ne répond pas", "Claude attend 2 réponses"]);
    expect(items[0]).toMatchObject({ tone: "bad", detail: "jeton de lecture Grafana absent", href: "/etat" });
  });

  it("tri par gravité : bad, warn, accent, neutral", () => {
    const items = todo(input([school({ online: false, lastSeen: NOW - 9999, licence: { status: "warning", tier: "x" } })], { paused: true, openQuestions: 1 }));
    expect(items.map((t) => t.tone)).toEqual(["bad", "warn", "accent", "neutral"]);
  });
});

describe("GET /", () => {
  it("rend l'accueil avec la flotte", async () => {
    const h = await harness(demoGrafana);
    const r = await get(h, "/");
    const html = await r.text();
    expect(r.status).toBe(200);
    for (const text of ["Écoles en ligne", "lycee-test ne répond plus", "Accueil", "État des services", "Dictées sur 24 h", "Tout voir"]) expect(html).toContain(text);
  });

  it("Grafana injoignable : la page s'affiche quand même", async () => {
    const h = await harness();
    const r = await get(h, "/");
    expect(r.status).toBe(200);
    expect(await r.text()).toContain("Grafana Cloud ne répond pas");
  });

  it("échappe le texte venu des données", async () => {
    const h = await harness(demoGrafana);
    await h.env.DB.prepare("INSERT INTO console_audit (at, by, action, detail) VALUES (?, ?, ?, ?)")
      .bind(NOW, "<script>alert(1)</script>", "<script>x</script>", "<img src=x onerror=y>").run();
    const html = await (await get(h, "/")).text();
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&#60;script&#62;");
  });
});

describe("GET /etat", () => {
  it("affiche les contrôles sans jamais montrer un secret", async () => {
    const h = await harness(demoGrafana);
    const r = await get(h, "/etat");
    const html = await r.text();
    expect(r.status).toBe(200);
    expect(html).toContain("Grafana Cloud");
    expect(html).toContain("Derniers envois de mesures");
    expect(html).not.toContain("BEGIN PRIVATE KEY");
    expect(html).not.toContain(h.env.SIGNING_KEY.split("\n")[1]);
  });

  it("ne casse pas quand tout est en panne", async () => {
    const h = await harness();
    const r = await get(h, "/etat");
    expect(r.status).toBe(200);
    expect(await r.text()).toContain("En panne");
  });
});

describe("signalements à traiter", () => {
  it("compte les 30 jours que montre la page Signalements, pas seulement 7", () => {
    const at = (days: number) => ({ at: NOW - days * 86400, school: "ipsa", line: { event: "support.report" } });
    expect(untreated([at(1), at(10), at(25)], new Set(), NOW)).toHaveLength(3);
  });
});
