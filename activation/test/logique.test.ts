// Bugs de logique et de données : chaque test a échoué avant sa correction.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ago, loadFleet, num, plural, type MetricsEnv } from "../src/dashboard";
import { invalid } from "../src/licence";
import { normalizeCode } from "../src/index";
import { when } from "../src/shell";
import { NOW, demoGrafana, get, harness, post, vector } from "./helpers";

afterEach(() => vi.unstubAllGlobals());
const text = async (r: Response) => (await r.text()).replace(/\s+/g, " ");
const base = { organization: "IPSA", tier: "1", kind: "pilot", starts: "2026-01-01", ends: "2026-12-31" };

describe("nombres et durées", () => {
  it("ago bascule en heures à 59 min 30 et en jours à 47 h 30, jamais négatif", () => {
    expect(ago(3599)).toBe("1 h");
    expect(ago(3540)).toBe("59 min");
    expect(ago(172799)).toBe("2 j");
    expect(ago(2 * 86400 - 3600)).toBe("47 h");
    expect(ago(-30)).toBe("0 min");
  });
  it("num n'affiche pas ∞", () => expect(num(Infinity)).toBe("—"));
  it("plural accorde", () => {
    expect(plural(1, "élève", "élèves")).toBe("1 élève");
    expect(plural(0, "élève", "élèves")).toBe("0 élève");
    expect(plural(2, "élève", "élèves")).toBe("2 élèves");
    expect(plural(undefined, "élève", "élèves")).toBe("— élèves");
  });
  it("un NaN de Prometheus ou une série sans école ne fausse pas les totaux", async () => {
    const h = await harness((url) => {
      const q = url.searchParams.get("query") ?? "";
      if (url.host !== "loki.test" && q.includes("nova_ai_spend_eur_month")) return vector([[{ school: "ipsa" }, 90], [{ school: "lycee-test" }, NaN]]);
      if (url.host !== "loki.test" && q.includes("timestamp(nova_uptime_seconds)")) return vector([[{ school: "ipsa" }, NOW - 30], [{ school: "lycee-test" }, NOW - 30], [{}, NOW - 30]]);
      return demoGrafana(url);
    });
    const html = await text(await get(h, "/"));
    expect(html).toContain('Dépense d&#39;IA du mois</div><div class="kpi-value">90 <small>€');
    expect(html).not.toContain("undefined");
  });
});

describe("licence", () => {
  it("refuse une enveloppe infinie ou illisible", () => {
    expect(invalid({ ...base, ai_envelope_eur: Infinity })).not.toBeNull();
    expect(invalid({ ...base, ai_envelope_eur: NaN })).not.toBeNull();
    expect(invalid({ ...base, ai_envelope_eur: 0 })).toBeNull();
  });
  it("normalizeCode ignore les tirets typographiques et espaces invisibles", () => {
    expect(normalizeCode("nova–7k2p—91xq​")).toBe("NOVA7K2P91XQ");
  });
  it("une clé de signature cassée ne brûle pas le jeton du formulaire", async () => {
    const h = await harness(demoGrafana);
    const good = h.env.SIGNING_KEY;
    const fields = { ...base, code_days: "30", nonce: "n-1" };
    h.env.SIGNING_KEY = "garbage";
    await post(h, "/codes", fields).catch(() => null);
    h.env.SIGNING_KEY = good;
    expect(await text(await post(h, "/codes", fields))).toContain("Code pour IPSA");
  });
  it("code_days à 1 : « 1 jour »", async () => {
    const h = await harness(demoGrafana);
    const html = await text(await post(h, "/codes", { ...base, code_days: "1", nonce: "n-2" }));
    expect(html).toContain("valable 1 jour,");
  });
});

describe("pages", () => {
  it("?page=1e20 ne fait pas échouer l'activité", async () => {
    const h = await harness(demoGrafana);
    expect((await get(h, "/activite?page=1e20")).status).toBe(200);
    expect((await get(h, "/activite?page=Infinity")).status).toBe(200);
  });
  it("la fiche d'une école retrouve ses codes même au-delà des 200 plus récents", async () => {
    const h = await harness(demoGrafana);
    const lic = (tier: string) => `x.${Buffer.from(JSON.stringify({ tier })).toString("base64url")}.y`;
    const ins = h.env.DB.prepare("INSERT INTO codes (code_hash, licence, school, expires_at) VALUES (?, ?, ?, ?)");
    await ins.bind("old".padEnd(64, "0"), lic("TIER-ANCIEN"), "IPSA", 10).run();
    for (let i = 0; i < 205; i++) await ins.bind(String(i).padEnd(64, "a"), lic("autre"), "Autre", NOW + 1000 + i).run();
    expect(await text(await get(h, "/ecoles/ipsa"))).toContain("TIER-ANCIEN");
  });
  it("Usage : le libellé ne promet pas une médiane qu'il ne calcule pas", async () => {
    const h = await harness(demoGrafana);
    const html = await text(await get(h, "/usage"));
    expect(html).not.toContain("Temps de transcription médian");
    expect(html).toContain("Temps de transcription, 95 %");
  });
  it("accords : 1 élève, 1 école vue, 1 signalement lu", async () => {
    const h = await harness((url) => {
      const q = url.searchParams.get("query") ?? "";
      if (url.host !== "loki.test" && q.includes("nova_active_users")) return vector([[{ school: "ipsa" }, 1], [{ school: "lycee-test" }, 1]]);
      if (url.host !== "loki.test" && q.includes("timestamp(nova_uptime_seconds)")) return vector([[{ school: "ipsa" }, NOW - 30]]);
      return demoGrafana(url);
    });
    expect(await text(await get(h, "/ecoles"))).toContain("1 élève actif<");
    const etat = await text(await get(h, "/etat"));
    expect(etat).toContain("1 école vue");
    expect(etat).toContain("1 signalement lu");
    expect(etat).toContain("0 code, 0 ligne de journal");
  });
});

describe("pilot", () => {
  it("une consigne avec retours à la ligne n'est pas tronquée par le CRLF des formulaires", async () => {
    const h = await harness(demoGrafana);
    const body = Array.from({ length: 20 }, () => "a".repeat(199)).join("\r\n"); // 3999 caractères côté navigateur
    await post(h, "/pilot/message", { body });
    const row = (await h.env.DB.prepare("SELECT body FROM pilot_messages").first()) as { body: string };
    expect(row.body.split("a").length - 1).toBe(3980);
  });
});

describe("journal d'activité", () => {
  it("les actions sur signalement montrent l'école et la date, pas la clé brute", async () => {
    const h = await harness(demoGrafana);
    await h.env.DB.prepare("INSERT INTO console_audit (at, by, action, detail) VALUES (?, 'sash', 'Signalement traité', ?)").bind(NOW, "ipsa-test:1794522369833").run();
    for (const path of ["/", "/activite"]) {
      const html = await text(await get(h, path));
      expect(html).not.toContain("1794522369833");
      expect(html).toContain(`ipsa-test · ${when(1794522369.833)}`);
    }
    const row = (await h.env.DB.prepare("SELECT detail FROM console_audit").first()) as { detail: string };
    expect(row.detail).toBe("ipsa-test:1794522369833");
  });
});

describe("séries journalières", () => {
  const env: MetricsEnv = { PROM_URL: "https://prom.test/api/prom", PROM_USER: "1", LOKI_URL: "https://loki.test", LOKI_USER: "2", GRAFANA_READ_TOKEN: "t" };
  function run(now: number, midnight: number) {
    const seen: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string) => {
      const u = new URL(input), q = u.searchParams.get("query") ?? "";
      seen.push(decodeURIComponent(input));
      const s = { school: "ipsa" };
      if (u.host === "loki.test") return new Response(JSON.stringify({ data: { result: [] } }));
      if (u.pathname.endsWith("query_range") && !q.includes("histogram")) return new Response(JSON.stringify({ data: { result: [{ metric: s, values: [[midnight - 86400, "4"], [midnight, "7"]] }] } }));
      if (q.includes("timestamp(nova_uptime_seconds)")) return new Response(JSON.stringify(vector([[s, now - 5]])));
      if (q.includes("nova_events_total") && !q.includes("[1d]")) return new Response(JSON.stringify(vector([[s, 3]])));
      return new Response(JSON.stringify(vector([])));
    }));
    return seen;
  }
  it("chaque barre = un jour calendaire de Paris, la dernière = aujourd'hui depuis minuit", async () => {
    const now = Date.UTC(2027, 0, 15, 8, 0) / 1000, midnight = Date.UTC(2027, 0, 14, 23) / 1000; // 09:00 Paris
    const seen = run(now, midnight);
    const r = await loadFleet(env, now);
    if (!r.ok) throw new Error(r.reason);
    expect(seen.some((q) => q.includes("query_range") && q.includes(`end=${midnight}`))).toBe(true);
    expect(seen.some((q) => q.includes("[32400s]"))).toBe(true);
    expect(r.fleet.schools[0].dictSeries).toEqual([[midnight - 86400 - 43200, 4], [midnight - 43200, 7], [now, 3]]);
  });
  it("le jour du passage à l'heure d'été (23 h), minuit reste minuit de Paris", async () => {
    const now = Date.UTC(2027, 2, 28, 10, 0) / 1000, midnight = Date.UTC(2027, 2, 27, 23) / 1000; // 12:00 Paris, 11 h écoulées
    const seen = run(now, midnight);
    await loadFleet(env, now);
    expect(seen.some((q) => q.includes("query_range") && q.includes(`end=${midnight}`))).toBe(true);
    expect(seen.some((q) => q.includes("[39600s]"))).toBe(true);
  });
});
