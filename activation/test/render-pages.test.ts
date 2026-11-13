// Enregistre chaque page et vue de la console en fichier HTML autonome, pour les ouvrir dans un vrai navigateur
// (scripts/render-audit.mjs). Inactif sauf si RENDER_DIR est défini : `RENDER_DIR=/chemin npx vitest run test/render-pages.test.ts`.
import { mkdirSync, writeFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { demoGrafana, get, harness, NOW, post, vector, type Harness } from "./helpers";

const DIR = process.env.RENDER_DIR;
const DAY = 86400;
const hex = (n: number) => String(n).padStart(64, "0");
const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
const licence = (c: Record<string, unknown>) => `e30.${b64(c)}.x`;

const LONG = "lycee-saint-exupery-des-grandes-ecoles-de-la-region-parisienne-et-banlieue-ouest";
const NASTY = `<b>&"'`;
const BLOB = "x".repeat(300);
const ids = [LONG, ...Array.from({ length: 28 }, (_, i) => `ecole-${String(i + 1).padStart(2, "0")}`), `ecole<i>&"'`];

// ---------------------------------------------------------------- Grafana
function extremeGrafana(url: URL): unknown {
  const q = url.searchParams.get("query") ?? "";
  const m = (i: number) => ({ school: ids[i] });
  const all = (f: (i: number) => number | null, extra: (i: number) => Record<string, string> = () => ({})) =>
    vector(ids.flatMap((_, i) => { const v = f(i); return v === null ? [] : [[{ ...m(i), ...extra(i) }, v] as [Record<string, string>, number]]; }));
  if (url.host === "loki.test") {
    const rep = (i: number, description: string | undefined, extra: Record<string, unknown> = {}) => ({ stream: { school: ids[i] }, values: [[String((NOW - 3600 * (i + 1)) * 1e9), JSON.stringify({ event: "support.report", app_version: "1.0.82", engines: { microphone: "ok" }, last_error: "", description, ...extra })]] });
    return { data: { result: [
      rep(0, BLOB, { last_error: BLOB, engines: { microphone: "error", transcription_model: "warning", "moteur-avec-un-nom-vraiment-trop-long-pour-tenir": "warning" }, app_version: "1.0.82-beta-" + "9".repeat(40) }),
      rep(1, `${NASTY} <script>alert(1)</script>`),
      rep(2, "Le micro coupe\nsur deux lignes"), rep(3, ""), rep(4, undefined),
      ...Array.from({ length: 12 }, (_, k) => rep(5 + k, `Signalement ${k}`)),
    ] } };
  }
  if (url.pathname.endsWith("query_range")) {
    const lat = q.includes("histogram");
    const series = (i: number): number[] | null => {
      if (i === 0) return Array.from({ length: 14 }, (_, d) => (d === 6 ? (lat ? 0.004 : 1234567) : 0));
      if (i === 1) return [lat ? 1.234 : 1234567];
      if (i === 2) return Array.from({ length: 14 }, () => 0);
      if (i === 3) return Array.from({ length: 14 }, (_, d) => (lat ? 0.0004 + d / 1e4 : d % 3 === 0 ? 0.5 : 0));
      if (i === 4) return Array.from({ length: 14 }, (_, d) => 1234567 * (d + 1));
      if (i === 5) return null;
      return Array.from({ length: 14 }, (_, d) => (lat ? 1.2 + d / 10 : 100 + d * 7));
    };
    return { data: { result: ids.flatMap((_, i) => { const s = series(i); if (!s) return []; return [{ metric: m(i), values: s.map((v, d) => [NOW - (s.length - 1 - d) * DAY, String(v)]) }]; }) } };
  }
  const age = (i: number) => [30, 7200, 3 * DAY, 40 * DAY, 90][i % 5];
  if (q.includes("timestamp(nova_uptime_seconds)")) return all((i) => NOW - age(i));
  if (q.includes("nova_server_info")) return all(() => 1, (i) => ({ server_version: i === 0 ? "10.20.30-rc.1+build." + "7".repeat(30) : "0.2." + i }));
  if (q.includes("nova_app_versions")) return vector(ids.flatMap((_, i) => [[{ ...m(i), version: i === 0 ? "1.0.82" : "1.0.80" }, i === 0 ? 1234567 : 12] as [Record<string, string>, number], ...(i < 3 ? [[{ ...m(i), version: "0.9.1-" + "z".repeat(50) }, 3] as [Record<string, string>, number]] : [])]));
  if (q.includes("nova_members")) return all((i) => (i === 0 ? 1234567 : i === 1 ? 0 : i * 13.5));
  if (q.includes("nova_active_users")) return all((i) => (i === 0 ? 1234567 : i === 1 ? 0 : i * 4));
  if (q.includes("nova_licence_info")) return all(() => 1, (i) => ({ status: ["valid", "warning", "grace", "expired", "none", "invalid", "bizarre"][i % 7], tier: i === 0 ? "1000+-tranche-vraiment-tres-longue-" + "t".repeat(40) : "151-250" }));
  if (q.includes("nova_licence_days_left")) return all((i) => (i === 0 ? 1234567 : i === 1 ? 0 : i === 2 ? -3 : 289.5));
  if (q.includes("nova_ai_spend_eur_month")) return all((i) => [1234567.891, 0, 0.004, 99.5, 15000, 80, 100, 101][i % 8]);
  if (q.includes("nova_ai_envelope_eur")) return all((i) => (i % 3 === 2 ? null : 1200 * (i === 0 ? 1000 : 1)));
  if (q.includes("nova_ai_tokens_total")) return all((i) => (i === 0 ? 1234567890 : i === 1 ? 0 : i * 1234.5));
  if (q.includes("nova_events_total")) return all((i) => (i === 0 ? 1234567 : i === 1 ? 0 : i * 3.7));
  return vector([]);
}

// ---------------------------------------------------------------- base
async function seed(h: Harness, extreme: boolean) {
  const run = (sql: string, ...a: unknown[]) => h.env.DB.prepare(sql).bind(...a).run();
  const c = { organization: "IPSA", tier: "151-250", kind: "subscription", starts: "2026-09-01", ends: "2027-08-31", ai_envelope_eur: 1200 };
  await run("INSERT INTO codes VALUES (?,?,?,?,NULL)", hex(1), licence(c), "ipsa", NOW + 20 * DAY);
  await run("INSERT INTO codes VALUES (?,?,?,?,?)", hex(2), licence(c), "IPSA", NOW + DAY, NOW - 5 * DAY);
  await run("INSERT INTO codes VALUES (?,?,?,?,NULL)", hex(3), licence({ ...c, organization: "Lycée Test", kind: "pilot", tier: "pilot" }), "Lycée Test", NOW - 3 * DAY);
  const t = (s: string) => (extreme ? s + BLOB : s);
  await run("INSERT INTO pilot_questions (at,title,details) VALUES (?,?,?)", NOW - 100, t("Quelle priorité ?"), t("Détails\nsur deux lignes"));
  await run("INSERT INTO pilot_questions (at,title,details,answer,answered_at,answered_by) VALUES (?,?,?,?,?,?)", NOW - 900, "Déjà tranchée", null, t("Oui"), NOW - 800, "sash@example.org");
  await run("INSERT INTO pilot_messages (at,by,body) VALUES (?,?,?)", NOW - 50, "sash@example.org", t("Priorité aux bugs"));
  await run("INSERT INTO pilot_messages (at,by,body,read_at,reply,replied_at) VALUES (?,?,?,?,?,?)", NOW - 500, "sash@example.org", `${NASTY} Autre`, NOW - 400, t("Fait"), NOW - 300);
  await run("INSERT INTO pilot_prs VALUES (?,?,?,?,?,?)", "nova-desktop", 1, "Première PR", "vert", "https://github.com/x/y/pull/1", NOW - 10);
  await run("INSERT INTO pilot_prs VALUES (?,?,?,?,?,?)", "nova-server", 2, t("Deuxième PR"), "rouge", "https://github.com/x/z/pull/2", NOW - 10);
  await run("INSERT INTO pilot_prs VALUES (?,?,?,?,?,?)", "nova-console", 3, "PR en cours", "en cours", "https://github.com/x/z/pull/3", NOW - 10);
  await run("INSERT INTO pilot_holds VALUES (?,?,?,?,?)", "nova-server", 2, t("à revoir"), NOW - 5, "sash@example.org");
  await run("INSERT INTO pilot_journal (at,kind,result,title,link,details) VALUES (?,?,?,?,?,?)", NOW - 60, "fusion", "ok", "PR fusionnée", null, "rapport\n".repeat(40));
  await run("INSERT INTO pilot_journal (at,kind,result,title,link,details) VALUES (?,?,?,?,?,?)", NOW - 120, "deploiement", "echec", t("Déploiement raté"), "https://example.com/run/1" + (extreme ? "/" + BLOB : ""), t("trace"));
  await run("INSERT INTO pilot_journal (at,kind,result,title) VALUES (?,?,?,?)", NOW - 180, "message", "info", "Note");
  for (let i = 0; i < 60; i++) await run("INSERT INTO console_audit (at,by,action,detail) VALUES (?,?,?,?)", NOW - 1000 - i * 60, "sash@example.org", `Action ${i}`, i % 2 ? "détail" : null);
  await run("INSERT INTO console_audit (at,by,action,detail) VALUES (?,?,?,?)", NOW - 3 * DAY, "sash@example.org", "Ancienne action", null);
  if (!extreme) return;
  for (let i = 0; i < 30; i++) await run("INSERT INTO codes VALUES (?,?,?,?,?)", hex(100 + i), licence({ ...c, organization: ids[i], tier: i % 4 ? "151-250" : "tranche-longue-" + "t".repeat(60), ai_envelope_eur: i % 2 ? 1234567 : undefined }), ids[i], NOW + ((i % 3) - 1) * DAY * 10, i % 3 === 0 ? NOW - DAY : null);
  await run("INSERT INTO console_audit (at,by,action,detail) VALUES (?,?,?,?)", NOW - 20, `${"adresse-tres-longue-".repeat(5)}@example.org`, `Action ${NASTY} ${BLOB}`, BLOB);
  await run("INSERT INTO pilot_journal (at,kind,result,title) VALUES (?,?,?,?)", NOW - 30, "type-de-journal-vraiment-tres-long-" + "k".repeat(40), "echec", BLOB);
}

// ---------------------------------------------------------------- pages
const pagesFor = (schools: string[], hash: string): [string, string][] => [
  ["accueil", "/"],
  ...["questions", "consignes", "pr", "journal"].map((v): [string, string] => [`claude-${v}`, `/claude?vue=${v}`]),
  ["claude-journal-echec", "/claude?vue=journal&resultat=echec"], ["claude-journal-ok-type", "/claude?vue=journal&resultat=ok&type=fusion"], ["claude-journal-type-vide", "/claude?vue=journal&type=rien"],
  ["claude-defaut", "/claude"],
  ["ecoles", "/ecoles"], ["ecoles-en-ligne", "/ecoles?vue=en-ligne"], ["ecoles-muettes", "/ecoles?vue=muettes"],
  ...schools.map((s, i): [string, string] => [`ecole-${i}`, `/ecoles/${encodeURIComponent(s)}`]),
  ["ecole-inconnue", "/ecoles/inconnue"],
  ["usage", "/usage"], ["usage-7j", "/usage?jours=7"], ["ia", "/ia"],
  ["signalements", "/signalements"], ["signalements-traites", "/signalements?vue=traites"], ["signalements-tous", "/signalements?vue=tous"],
  ["signalements-ecole", `/signalements?vue=tous&ecole=${encodeURIComponent(schools[0] ?? "ipsa")}`],
  ["licences", "/licences"], ["licences-attente", "/licences?etat=attente"], ["licences-actives", "/licences?etat=actives"], ["licences-expires", "/licences?etat=expires"],
  ["licences-recherche", "/licences?q=ipsa"], ["licences-recherche-vide", "/licences?q=zzz"], ["licences-renouveler", `/licences?renouveler=${hash}`],
  ["activite", "/activite"], ["activite-p2", "/activite?page=2"], ["activite-claude", "/activite?source=claude"], ["activite-console", "/activite?source=console"], ["activite-p9", "/activite?page=9"],
  ["etat", "/etat"],
];

const sets: { name: string; grafana?: (u: URL) => unknown; db: false | "demo" | "extreme"; schools: string[]; hash: string }[] = [
  { name: "a-demo", grafana: demoGrafana, db: "demo", schools: ["ipsa", "lycee-test"], hash: hex(1) },
  { name: "b-vide", db: false, schools: ["ipsa"], hash: hex(1) },
  { name: "b2-flotte-vide", grafana: () => vector([]), db: false, schools: ["ipsa"], hash: hex(1) },
  { name: "c-extremes", grafana: extremeGrafana, db: "extreme", schools: [ids[0], ids[1], ids[2], ids[3], ids[4], ids[5], ids[29]], hash: hex(100) },
];

describe.skipIf(!DIR)("rendu des pages", () => {
  for (const s of sets) {
    it(s.name, async () => {
      const h = await harness(s.grafana);
      if (s.db) await seed(h, s.db === "extreme");
      const out = `${DIR}/${s.name}`;
      mkdirSync(out, { recursive: true });
      for (const [slug, path] of pagesFor(s.schools, s.hash)) {
        const r = await get(h, path);
        expect(r.status, path).toBe(200);
        writeFileSync(`${out}/${slug}.html`, await r.text());
      }
      // notices d'erreur et de succès après création de code
      const bad = await post(h, "/codes", { organization: "", tier: "x", kind: "pilot", starts: "2026-01-01", ends: "2025-01-01" });
      writeFileSync(`${out}/notice-erreur.html`, await bad.text());
      const fields = { organization: s.db === "extreme" ? LONG : "Lycée Neuf", tier: "151-250", kind: "subscription", starts: "2026-09-01", ends: "2027-08-31", ai_envelope_eur: "1200", code_days: "30", nonce: "n1" };
      writeFileSync(`${out}/notice-ok.html`, await (await post(h, "/codes", fields)).text());
      writeFileSync(`${out}/notice-deja.html`, await (await post(h, "/codes", fields)).text());
    }, 60000);
  }
});
