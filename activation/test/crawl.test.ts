// Robot : exerce toute la console comme un navigateur (liens, formulaires, en-têtes) sur une base remplie.
import { beforeAll, describe, expect, it } from "vitest";
import consoleWorker from "../src/console";
import { PAGES } from "../src/pages";
import { NAV } from "../src/shell";
import { demoGrafana, get, harness, NOW, ORIGIN, post, type Harness } from "./helpers";

const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
const licence = (c: Record<string, unknown>) => `e30.${b64(c)}.x`;
const day = (s: number) => new Date(s * 1000).toISOString().slice(0, 10);
const DAY = 86400;
const hex = (n: number) => String(n).padStart(64, "0");
const TODAY = day(NOW);

const decode = (s: string) => s.replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)).replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const attrs = (tag: string) => Object.fromEntries([...tag.matchAll(/([\w:-]+)(?:="([^"]*)")?/g)].map((m) => [m[1], decode(m[2] ?? "")]));
const visibleText = (html: string) => decode(html.replace(/<style[\s\S]*?<\/style>/g, "").replace(/<[^>]*>/g, " "));

interface Field { name: string; type: string; value: string; required: boolean; min?: number; max?: number; options?: string[]; selected?: string; tag: string }
interface Form { page: string; method: string; action: string; fields: Field[]; buttons: { name: string; value: string }[] }

function parseForms(html: string, page: string): Form[] {
  return [...html.matchAll(/<form\b([^>]*)>([\s\S]*?)<\/form>/g)].map((m) => {
    const f = attrs(m[1]);
    const fields: Field[] = [], buttons: Form["buttons"] = [];
    for (const i of m[2].matchAll(/<input\b([^>]*)>/g)) {
      const a = attrs(i[1]);
      if (a.name) fields.push({ name: a.name, type: a.type ?? "text", value: a.value ?? "", required: "required" in a, min: a.min ? +a.min : undefined, max: a.max ? +a.max : undefined, tag: "input" });
    }
    for (const t of m[2].matchAll(/<textarea\b([^>]*)>/g)) { const a = attrs(t[1]); fields.push({ name: a.name, type: "textarea", value: "", required: "required" in a, tag: "textarea" }); }
    for (const s of m[2].matchAll(/<select\b([^>]*)>([\s\S]*?)<\/select>/g)) {
      const opts = [...s[2].matchAll(/<option\b([^>]*)>/g)].map((o) => attrs(o[1]));
      fields.push({ name: attrs(s[1]).name, type: "select", value: "", required: true, options: opts.map((o) => o.value), selected: opts.find((o) => "selected" in o)?.value, tag: "select" });
    }
    for (const b of m[2].matchAll(/<button\b([^>]*)>/g)) { const a = attrs(b[1]); if (a.name) buttons.push({ name: a.name, value: a.value ?? "" }); }
    return { page, method: (f.method ?? "get").toLowerCase(), action: new URL(f.action ?? page, ORIGIN + page).pathname, fields, buttons };
  });
}

/** Corps comme un navigateur : champs cachés, première option (ou selected), valeurs plausibles pour les champs requis ou de recherche. */
function fill(form: Form, button?: { name: string; value: string }): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of form.fields) {
    let v = f.value;
    if (f.tag === "select") v = f.selected ?? f.options![0] ?? "";
    else if (!v && f.type === "date") v = TODAY;
    else if (!v && f.type === "number") v = String(f.min ?? 1);
    else if (!v && (f.required || f.type === "search")) v = f.type === "search" ? "ipsa" : "Test robot";
    if (f.type === "number" && v) v = String(Math.min(f.max ?? Infinity, Math.max(f.min ?? -Infinity, +v)));
    out[f.name] = v;
  }
  if (button) out[button.name] = button.value;
  return out;
}

let h: Harness;
const pages = new Map<string, { status: number; type: string; html: string; referrer: string | null }>();
const forms: Form[] = [];
const problems: string[] = [];
let submitted = 0;
const auditCount = async () => (await h.env.DB.prepare("SELECT COUNT(*) AS n FROM console_audit").first<{ n: number }>())!.n;

async function seed() {
  const run = (sql: string, ...a: unknown[]) => h.env.DB.prepare(sql).bind(...a).run();
  const c = { organization: "IPSA", tier: "151-250", kind: "subscription", starts: "2026-09-01", ends: "2027-08-31", ai_envelope_eur: 1200 };
  await run("INSERT INTO codes VALUES (?,?,?,?,NULL)", hex(1), licence(c), "ipsa", NOW + 20 * DAY);
  await run("INSERT INTO codes VALUES (?,?,?,?,?)", hex(2), licence(c), "IPSA", NOW + DAY, NOW - 5 * DAY);
  await run("INSERT INTO codes VALUES (?,?,?,?,NULL)", hex(3), licence({ ...c, organization: "Lycée Test", kind: "pilot", tier: "pilot" }), "Lycée Test", NOW - 3 * DAY);
  await run("INSERT INTO pilot_questions (at,title,details) VALUES (?,?,?)", NOW - 100, "Quelle priorité ?", "Détails\nsur deux lignes");
  await run("INSERT INTO pilot_questions (at,title,details,answer,answered_at,answered_by) VALUES (?,?,?,?,?,?)", NOW - 900, "Déjà tranchée", null, "Oui", NOW - 800, "sash@example.org");
  await run("INSERT INTO pilot_messages (at,by,body) VALUES (?,?,?)", NOW - 50, "sash@example.org", "Priorité aux bugs");
  await run("INSERT INTO pilot_messages (at,by,body,read_at,reply,replied_at) VALUES (?,?,?,?,?,?)", NOW - 500, "sash@example.org", "Autre", NOW - 400, "Fait", NOW - 300);
  await run("INSERT INTO pilot_prs VALUES (?,?,?,?,?,?)", "nova-desktop", 1, "Première PR", "vert", "https://github.com/x/y/pull/1", NOW - 10);
  await run("INSERT INTO pilot_prs VALUES (?,?,?,?,?,?)", "nova-server", 2, "Deuxième PR", "rouge", "https://github.com/x/z/pull/2", NOW - 10);
  await run("INSERT INTO pilot_holds VALUES (?,?,?,?,?)", "nova-server", 2, "à revoir", NOW - 5, "sash@example.org");
  await run("INSERT INTO pilot_journal (at,kind,result,title,link,details) VALUES (?,?,?,?,?,?)", NOW - 60, "fusion", "ok", "PR fusionnée", null, "rapport");
  await run("INSERT INTO pilot_journal (at,kind,result,title,link,details) VALUES (?,?,?,?,?,?)", NOW - 120, "deploiement", "echec", "Déploiement raté", "https://example.com/run/1", "trace");
  await run("INSERT INTO pilot_journal (at,kind,result,title) VALUES (?,?,?,?)", NOW - 180, "message", "info", "Note");
  for (let i = 0; i < 60; i++) await run("INSERT INTO console_audit (at,by,action,detail) VALUES (?,?,?,?)", NOW - 1000 - i * 60, "sash@example.org", `Action ${i}`, i % 2 ? "détail" : null);
  await run("INSERT INTO console_audit (at,by,action,detail) VALUES (?,?,?,?)", NOW - 3 * DAY, "sash@example.org", "Ancienne action", null);
}

const internal = (html: string, from: string): string[] => [...html.matchAll(/<a\b([^>]*)>/g)].flatMap((m) => {
  const href = attrs(m[1]).href;
  if (!href || href.startsWith("#") || /^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith("//")) return [];
  const u = new URL(href, ORIGIN + from);
  return u.origin === ORIGIN ? [u.pathname + u.search] : [];
});

beforeAll(async () => {
  h = await harness(demoGrafana);
  await seed();
  const queue = NAV.flatMap((g) => g.items.map((i) => i.href));
  const seen = new Set<string>();
  while (queue.length && seen.size < 300) {
    const path = queue.shift()!;
    if (seen.has(path)) continue;
    seen.add(path);
    const r = await get(h, path);
    const body = await r.text();
    pages.set(path, { status: r.status, type: r.headers.get("content-type") ?? "", html: body, referrer: r.headers.get("referrer-policy") });
    if (r.status === 200 && r.headers.get("content-type")?.includes("html")) {
      queue.push(...internal(body, path));
      forms.push(...parseForms(body, path));
    }
  }
});

describe("crawl GET", () => {
  it("visite toute la navigation et tous les liens", () => {
    expect(pages.size).toBeGreaterThan(NAV.flatMap((g) => g.items).length);
    for (const g of NAV) for (const i of g.items) expect(pages.has(i.href), i.href).toBe(true);
    console.log(`robot : ${pages.size} pages visitées, ${forms.length} formulaires découverts : ${[...pages.keys()].join(" ")}`);
  });

  it("chaque page répond 200, complète et propre", () => {
    for (const [path, p] of pages) {
      const csv = path.split("?")[0].endsWith(".csv");
      if (p.status !== 200) problems.push(`${path} : statut ${p.status}`);
      if (p.html.includes("Introuvable")) problems.push(`${path} : « Introuvable »`);
      if (csv) { if (!p.type.startsWith("text/csv")) problems.push(`${path} : type ${p.type}`); continue; }
      const side = p.html.match(/<aside[\s\S]*?<\/aside>/)?.[0] ?? "";
      const current = (side.match(/aria-current="page"/g) ?? []).length;
      if (current !== 1) problems.push(`${path} : ${current} entrées aria-current`);
      const total = (p.html.match(/aria-current="page"/g) ?? []).length;
      if (total !== 1) problems.push(`${path} : ${total} aria-current="page" dans la page`);
      if (!/<title>[^<]+<\/title>/.test(p.html)) problems.push(`${path} : pas de <title>`);
      for (const bad of visibleText(p.html).match(/undefined|\bNaN\b|\[object Object\]|\bnull\b/g) ?? []) problems.push(`${path} : « ${bad} » affiché`);
      if (p.referrer !== "same-origin") problems.push(`${path} : referrer-policy ${p.referrer}`);
    }
    expect(problems).toEqual([]);
  });
});

describe("formulaires", () => {
  it("soumet chaque formulaire comme un navigateur", async () => {
    const bad: string[] = [];
    const reached = new Set<string>();
    const done = new Set<string>(); // une même soumission vue sur plusieurs pages (ex. répondre à la même question) ne se rejoue pas : /pilot/answer ignore une 2e réponse, à raison
    for (const f of forms) {
      for (const button of f.buttons.length > 1 ? f.buttons : [f.buttons[0]]) {
        const body = fill(f, button);
        const label = `${f.method.toUpperCase()} ${f.action} (depuis ${f.page}) ${JSON.stringify(body)}`;
        if (f.method === "post" && done.has(label.replace(/ \(depuis [^)]*\)/, ""))) continue;
        done.add(label.replace(/ \(depuis [^)]*\)/, ""));
        submitted++;
        if (f.method === "get") {
          const r = await get(h, `${f.action}?${new URLSearchParams(body)}`);
          if (r.status !== 200) bad.push(`${label} -> ${r.status}`);
          continue;
        }
        reached.add(f.action);
        const before = await auditCount();
        const r = await post(h, f.action, body);
        if (r.status !== 303 && r.status !== 200) { bad.push(`${label} -> ${r.status}`); continue; }
        if (r.status === 200 && /class="notice bad"/.test(await r.text())) bad.push(`${label} -> refus affiché`);
        if (r.status === 303) {
          const next = new URL(r.headers.get("location")!, ORIGIN + f.action);
          const g = await get(h, next.pathname + next.search);
          if (g.status !== 200) bad.push(`${label} -> redirection ${next.pathname + next.search} : ${g.status}`);
        }
        if ((await auditCount()) <= before) bad.push(`${label} -> aucune trace console_audit`);
      }
    }
    expect(bad).toEqual([]);
    const missing = PAGES.flatMap((p) => Object.keys(p.actions ?? {})).filter((a) => !reached.has(a));
    expect(missing, "actions POST sans formulaire").toEqual([]);
    console.log(`robot : ${submitted} soumissions`);
  });
});

describe("comme Chrome", () => {
  const raw = (headers: Record<string, string>) => consoleWorker.fetch(new Request(`${ORIGIN}/pilot/pause`, {
    method: "POST", body: "paused=1", headers: { "cf-access-jwt-assertion": h.token, "content-type": "application/x-www-form-urlencoded", ...headers },
  }), h.env);
  it("POST sans Origin ou avec Origin: null refusé", async () => {
    expect((await raw({})).status).toBe(403);
    expect((await raw({ origin: "null" })).status).toBe(403);
    expect((await raw({ origin: "https://evil.test" })).status).toBe(403);
    expect((await raw({ origin: ORIGIN })).status).toBe(303);
  });
});

describe("boutons dont le résultat est invisible au robot", () => {
  const page = async (path: string) => (await get(h, path)).text();
  const hrefs = (html: string) => [...html.matchAll(/<a\b([^>]*)>/g)].map((m) => attrs(m[1]).href).filter(Boolean);
  const value = (html: string, name: string) => decode(html.match(new RegExp(`<input name="${name}"[^>]*? value="([^"]*)"`))?.[1] ?? "");

  it("Renouveler pré-remplit le formulaire de création", async () => {
    const html = await page("/licences");
    const link = hrefs(html).find((u) => u.startsWith(`/licences?renouveler=1#`))!;
    expect(link).toContain("#nouveau");
    const next = await page(link.split("#")[0]);
    expect(value(next, "organization")).toBe("IPSA");
    expect(value(next, "tier")).toBe("151-250");
    expect(value(next, "starts")).toBe("2027-09-01");
    expect(value(next, "ends")).toBe("2028-08-31");
    expect(value(next, "ai_envelope_eur")).toBe("1200");
    expect(next).toContain("Renouvellement de IPSA");
  });

  it("Exporter (CSV) renvoie un fichier avec toutes les lignes", async () => {
    expect(hrefs(await page("/licences"))).toContain("/licences.csv");
    const r = await get(h, "/licences.csv");
    expect(r.headers.get("content-disposition")).toContain("attachment");
    const lines = (await r.text()).trim().split("\r\n");
    const n = (await h.env.DB.prepare("SELECT COUNT(*) AS n FROM codes").first<{ n: number }>())!.n; // les formulaires ont pu en créer
    expect(lines).toHaveLength(n + 1);
    expect(lines.join("\n")).toMatch(/ipsa;151-250;subscription;2026-09-01;2027-08-31;1200;/i);
  });

  it("les filtres de Signalements gardent ?vue et ?ecole", async () => {
    const links = hrefs(await page("/signalements?vue=tous&ecole=ipsa"));
    expect(links).toContain("/signalements?vue=a-traiter&ecole=ipsa");
    expect(links).toContain("/signalements?vue=traites&ecole=ipsa");
    expect(links).toContain("/signalements?vue=tous");
  });

  it("le journal de Claude garde résultat et type, les filtres de licences gardent la recherche", async () => {
    const links = hrefs(await page("/claude?vue=journal&resultat=echec&type=deploiement"));
    expect(links).toContain("/claude?vue=journal&type=deploiement");
    expect(links).toContain("/claude?vue=journal&resultat=echec");
    expect(hrefs(await page("/licences?etat=attente&q=ipsa"))).toContain("/licences?q=ipsa");
  });

  it("la pagination de l'Activité avance, recule et garde la source", async () => {
    const p1 = hrefs(await page("/activite?source=console"));
    expect(p1).toContain("/activite?source=console&page=2");
    const p2 = await page("/activite?source=console&page=2");
    expect(hrefs(p2)).toContain("/activite?source=console");
    expect(p2).toContain("Page 2");
    expect(hrefs(p2)).not.toContain("/activite?source=console&page=3");
  });

  it("les onglets de Claude affichent chacun leur contenu", async () => {
    expect(await page("/claude?vue=questions")).toContain("Quelle priorité ?");
    expect(await page("/claude?vue=consignes")).toContain("Priorité aux bugs");
    expect(await page("/claude?vue=pr")).toContain("Deuxième PR");
    expect(await page("/claude?vue=journal")).toContain("Déploiement raté");
    expect(await page("/claude?vue=n-importe-quoi")).toContain('class="on" aria-current="true"');
  });
});

describe("sans Grafana ni données", () => {
  it("toutes les pages de la navigation restent propres", async () => {
    const empty = await harness(); // Grafana répond 503, base vide
    for (const i of NAV.flatMap((g) => g.items)) {
      const r = await get(empty, i.href);
      const html = await r.text();
      expect(r.status, i.href).toBe(200);
      expect(visibleText(html).match(/undefined|NaN|\[object Object\]|null/g), i.href).toBeNull();
      expect(html.match(/aria-current="page"/g), i.href).toHaveLength(1);
    }
  });
});
