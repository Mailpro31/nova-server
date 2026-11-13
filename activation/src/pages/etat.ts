import { ago, escape, loadFleet, plural } from "../dashboard";
import { pill, type Ctx, type Page } from "../shell";

type Check = { name: string; state: "ok" | "bad" | "off"; detail: string };
const PILL = { ok: pill("ok", "OK"), bad: pill("bad", "En panne"), off: pill("neutral", "Non configuré") };
const ACTIVATION = "https://nova-activation.nova-ecole.workers.dev/";

const count = async (ctx: Ctx, table: string) =>
  (await ctx.env.DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>())?.n ?? 0;

async function render(ctx: Ctx) {
  const fleet = await loadFleet(ctx.env, ctx.now).catch(() => ({ ok: false as const, reason: "grafana" as const }));
  const f = fleet.ok ? fleet.fleet : null;
  const noToken = !fleet.ok && fleet.reason === "token";
  const checks: Check[] = [];

  checks.push(f
    ? { name: "Grafana Cloud (métriques)", state: "ok", detail: plural(f.schools.length, "école vue", "écoles vues") }
    : { name: "Grafana Cloud (métriques)", state: noToken ? "off" : "bad", detail: noToken ? "jeton de lecture absent" : "aucune réponse" });
  checks.push(f?.reports
    ? { name: "Journaux (Loki)", state: "ok", detail: plural(f.reports.length, "signalement lu", "signalements lus") }
    : { name: "Journaux (Loki)", state: f ? "bad" : "off", detail: f ? "ajoutez le droit logs:read au jeton" : "dépend de Grafana Cloud" });

  try {
    await ctx.env.DB.prepare("SELECT 1").first();
    checks.push({ name: "Base de données (D1)", state: "ok", detail: `${plural(await count(ctx, "codes"), "code", "codes")}, ${plural(await count(ctx, "pilot_journal"), "ligne de journal", "lignes de journal")}` });
  } catch {
    checks.push({ name: "Base de données (D1)", state: "bad", detail: "la requête a échoué" });
  }

  try {
    const r = await fetch(ACTIVATION, { signal: AbortSignal.timeout(3000) });
    checks.push({ name: "Service d'activation public", state: r.status < 500 ? "ok" : "bad", detail: r.status < 500 ? "joignable" : `réponse ${r.status}` });
  } catch {
    checks.push({ name: "Service d'activation public", state: "bad", detail: "injoignable (délai de 3 s)" });
  }

  // Booléens seulement : jamais la valeur d'un secret.
  const missing = (["SIGNING_KEY", "GRAFANA_READ_TOKEN"] as const).filter((k) => !ctx.env[k]);
  checks.push({ name: "Secrets", state: missing.length ? "off" : "ok", detail: missing.length ? `absent : ${missing.join(", ")}` : "SIGNING_KEY et GRAFANA_READ_TOKEN présents" });
  checks.push({ name: "Cloudflare Access", state: "ok", detail: `connecté en tant que ${ctx.user}` });

  const table = f?.schools.length
    ? `<div class="card flush scroll"><table class="cards"><thead><tr><th>École</th><th>Dernier envoi</th><th>État</th></tr></thead><tbody>${f.schools.map((s) =>
      `<tr><td data-label="École"><strong>${escape(s.id)}</strong></td><td data-label="Dernier envoi">il y a ${escape(ago(ctx.now - s.lastSeen))}</td><td data-label="État">${s.online ? pill("ok", "En ligne") : pill("bad", "Muet")}</td></tr>`).join("")}</tbody></table></div>`
    : `<p class="empty">Aucune mesure reçue.</p>`;

  return {
    title: "État des services",
    subtitle: "Contrôles en direct des services dont dépend la console.",
    body: `<section><div class="grid">${checks.map((c) =>
      `<article class="card"><header><h3>${escape(c.name)}</h3>${PILL[c.state]}</header><p class="muted">${escape(c.detail)}</p></article>`).join("")}</div></section>
<section><h2>Derniers envois de mesures</h2>${table}</section>`,
  };
}

export const etat: Page = { id: "etat", path: "/etat", render };
