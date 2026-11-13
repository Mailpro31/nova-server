import { afterEach, describe, expect, it, vi } from "vitest";
import { dayLabel } from "../../src/pages/activite";
import { NOW, get, harness, post, type Harness } from "../helpers";

afterEach(() => vi.unstubAllGlobals());

const text = async (h: Harness, path: string) => (await get(h, path)).text();
const audits = async (h: Harness) =>
  (await h.env.DB.prepare("SELECT by, action FROM console_audit ORDER BY id").all<{ by: string; action: string }>()).results;
const sql = (h: Harness, query: string, ...args: unknown[]) => h.env.DB.prepare(query).bind(...args).run();

describe("Claude : pause", () => {
  it("met en pause puis relance, avec deux lignes d'audit signées", async () => {
    const h = await harness();
    expect((await post(h, "/pilot/pause", { paused: "1" })).status).toBe(303);
    const html = await text(h, "/claude");
    expect(html).toContain("En pause");
    expect(html).toContain("Relancer Claude");
    expect(html).toContain("reconfirmé dans le chat");
    await post(h, "/pilot/pause", { paused: "0" });
    expect(await text(h, "/claude")).toContain("Mettre Claude en pause");
    expect(await audits(h)).toEqual([
      { by: "sash@example.org", action: "Claude mis en pause" },
      { by: "sash@example.org", action: "Claude relancé" },
    ]);
  });
});

describe("Claude : consignes et questions", () => {
  it("affiche la consigne échappée et refuse une origine étrangère", async () => {
    const h = await harness();
    expect((await post(h, "/pilot/message", { body: "<img src=x>" }, "https://ailleurs.test")).status).toBe(403);
    expect(await h.env.DB.prepare("SELECT * FROM pilot_messages").first()).toBeNull();
    const res = await post(h, "/pilot/message", { body: "<img src=x>" });
    expect(res.headers.get("location")).toBe("/claude?vue=consignes");
    const html = await text(h, "/claude?vue=consignes");
    expect(html).not.toContain("<img src=x>");
    expect(html).toContain("Pas encore lu");
    expect((await audits(h)).map((a) => a.action)).toEqual(["Consigne envoyée"]);
  });

  it("répond une seule fois ; l'onglet par défaut est Questions quand une question est ouverte", async () => {
    const h = await harness();
    expect(await text(h, "/claude")).toContain("Nouvelle consigne");
    await sql(h, "INSERT INTO pilot_questions (at, title) VALUES (?, 'Prix ?')", NOW);
    expect(await text(h, "/claude")).toContain("Votre réponse");
    await post(h, "/pilot/answer", { id: "1", answer: "12 €" });
    await post(h, "/pilot/answer", { id: "1", answer: "autre" });
    expect(await h.env.DB.prepare("SELECT answer, answered_by FROM pilot_questions").first()).toEqual({ answer: "12 €", answered_by: "sash@example.org" });
    expect(await audits(h)).toHaveLength(1);
    const html = await text(h, "/claude?vue=questions");
    expect(html).toContain("12 €");
    expect(html).toContain("Répondu par sash@example.org");
  });
});

describe("Claude : PR", () => {
  it("retient puis relâche, et refuse un dépôt ou un numéro invalide", async () => {
    const h = await harness();
    await sql(h, "INSERT INTO pilot_prs VALUES ('nova-server', 12, 'feat: x', 'vert', 'https://github.com/x', ?)", NOW);
    await post(h, "/pilot/hold", { repo: "nova-server", number: "12", hold: "1", reason: "attend Sash" });
    expect(await text(h, "/claude?vue=pr")).toContain("Retenue : attend Sash");
    await post(h, "/pilot/hold", { repo: "nova-server", number: "12", hold: "0" });
    expect(await text(h, "/claude?vue=pr")).toContain("Autorisée si verte");
    expect((await post(h, "/pilot/hold", { repo: "../x", number: "1", hold: "1" })).status).toBe(400);
    expect((await post(h, "/pilot/hold", { repo: "nova-server", number: "0", hold: "1" })).status).toBe(400);
    expect((await audits(h)).map((a) => a.action)).toEqual(["PR retenue nova-server #12", "PR relâchée nova-server #12"]);
  });
});

describe("Claude : journal", () => {
  it("filtre par résultat, échappe le rapport et ne rend cliquable que https", async () => {
    const h = await harness();
    await sql(h, "INSERT INTO pilot_journal (at, kind, result, title, link, details) VALUES (?, 'bug', 'echec', 'Bulle cassée', 'javascript:alert(1)', 'trace: <err>')", NOW);
    await sql(h, "INSERT INTO pilot_journal (at, kind, result, title, link) VALUES (?, 'fusion', 'ok', 'PR fusionnée', 'https://github.com/x/pull/1')", NOW);
    const failed = await text(h, "/claude?vue=journal&resultat=echec");
    expect(failed).toContain("Bulle cassée");
    expect(failed).not.toContain("PR fusionnée");
    expect(failed).toContain("trace: &#60;err&#62;");
    expect(failed).not.toContain("<err>");
    expect(failed).not.toContain('href="javascript:');
    const all = await text(h, "/claude?vue=journal");
    expect(all).toContain('href="https://github.com/x/pull/1" target="_blank" rel="noopener"');
    expect(await text(h, "/claude?vue=journal&type=fusion")).not.toContain("Bulle cassée");
  });
});

describe("Activité", () => {
  it("mélange console et Claude, filtre par source", async () => {
    const h = await harness();
    await post(h, "/pilot/pause", { paused: "1" });
    await sql(h, "INSERT INTO pilot_journal (at, kind, result, title) VALUES (?, 'deploiement', 'echec', 'Déploiement raté')", NOW);
    const all = await text(h, "/activite");
    expect(all).toContain("Claude mis en pause");
    expect(all).toContain("Déploiement raté");
    expect(all).toContain("Aujourd&#39;hui");
    expect(all).toContain("par sash@example.org");
    expect(all).toContain('href="/claude?vue=journal"');
    const claude = await text(h, "/activite?source=claude");
    expect(claude).toContain("Déploiement raté");
    expect(claude).not.toContain("Claude mis en pause");
    expect(await text(h, "/activite?source=console")).not.toContain("Déploiement raté");
  });

  it("pagine à 50 lignes", async () => {
    const h = await harness();
    for (let i = 0; i < 60; i++) await sql(h, "INSERT INTO console_audit (at, by, action) VALUES (?, 'a@b.c', ?)", NOW - i, `Action ${i}`);
    const first = await text(h, "/activite");
    expect(first.match(/<li class="card">/g)).toHaveLength(50);
    expect(first).toContain("Plus ancien");
    expect(first).not.toContain("Plus récent");
    const second = await text(h, "/activite?page=2");
    expect(second.match(/<li class="card">/g)).toHaveLength(10);
    expect(second).toContain("Plus récent");
    expect(second).not.toContain("Plus ancien");
  });
});

describe("dayLabel", () => {
  // 2026-11-13 12:00 à Paris (UTC+1)
  const now = Date.UTC(2026, 10, 13, 11, 0) / 1000;
  it("aujourd'hui, hier, un autre jour", () => {
    expect(dayLabel(now - 3600, now)).toBe("Aujourd'hui");
    expect(dayLabel(now - 86400, now)).toBe("Hier");
    expect(dayLabel(Date.UTC(2026, 10, 7, 11, 0) / 1000, now)).toBe("samedi 7 novembre 2026");
  });
  it("change de jour à minuit, heure de Paris", () => {
    const justAfter = Date.UTC(2026, 10, 12, 23, 30) / 1000; // 00:30 le 13 à Paris
    const justBefore = Date.UTC(2026, 10, 12, 22, 30) / 1000; // 23:30 le 12 à Paris
    expect(dayLabel(justAfter, now)).toBe("Aujourd'hui");
    expect(dayLabel(justBefore, now)).toBe("Hier");
  });
  it("tient au passage à l'heure d'hiver", () => {
    const sunday = Date.UTC(2026, 9, 25, 12, 0) / 1000; // 25 oct. 2026, journée de 25 h
    expect(dayLabel(Date.UTC(2026, 9, 24, 12, 0) / 1000, sunday)).toBe("Hier");
  });
});
