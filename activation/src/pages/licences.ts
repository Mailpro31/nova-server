import { claimsOf, invalid, newCode, signLicence, type LicenceRequest } from "../licence";
import { escape, plural } from "../dashboard";
import { normalizeCode, sha256Hex } from "../index";
import { audit, empty, kpi, layout, notice, pill, redirect, type Ctx, type Page, type View } from "../shell";

const DAY = 86400;
const day = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toISOString().slice(0, 10) : "");
const HASH = /^[0-9a-f]{64}$/;

interface Row { code_hash: string; school: string; licence: string; expires_at: number; used_at: number | null }
type Etat = "attente" | "actives" | "expires";
const stateOf = (r: Row, now: number): Etat => (r.used_at ? "actives" : r.expires_at > now ? "attente" : "expires");

/** Valeurs du formulaire pour renouveler une licence : début = lendemain de l'ancienne fin, fin = début + 1 an − 1 jour. */
export function renewal(claims: Record<string, unknown>): LicenceRequest | null {
  const ends = String(claims.ends ?? "");
  const old = new Date(`${ends}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(ends) || Number.isNaN(old.getTime())) return null;
  const start = new Date(old.getTime() + DAY * 1000);
  const end = new Date(Date.UTC(start.getUTCFullYear() + 1, start.getUTCMonth(), start.getUTCDate()) - DAY * 1000);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const envelope = Number(claims.ai_envelope_eur);
  return {
    organization: String(claims.organization ?? ""), tier: String(claims.tier ?? ""), kind: String(claims.kind ?? "subscription"),
    starts: iso(start), ends: iso(end),
    ...(claims.ai_envelope_eur !== undefined && Number.isFinite(envelope) ? { ai_envelope_eur: envelope } : {}),
  };
}

/** Cellule CSV : protégée contre l'injection de formule, guillemets doublés au besoin. */
export function csvCell(value: unknown): string {
  let s = String(value ?? "");
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[;"\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

const ETAT_LABEL: Record<Etat, string> = { attente: "en attente", actives: "activé", expires: "expiré" };

function csv(rows: Row[], now: number): Response {
  const head = "etablissement;offre;type;debut;fin;enveloppe_eur;etat;code_valable_jusqu_au;active_le";
  const lines = rows.map((r) => {
    const c = claimsOf(r.licence);
    return [r.school, c.tier, c.kind, c.starts, c.ends, c.ai_envelope_eur, ETAT_LABEL[stateOf(r, now)], day(r.expires_at), day(r.used_at)].map(csvCell).join(";");
  });
  return new Response(`﻿${[head, ...lines].join("\r\n")}\r\n`, {
    headers: { "content-type": "text/csv; charset=utf-8", "content-disposition": 'attachment; filename="codes-nova.csv"', "cache-control": "no-store" },
  });
}

const LIST_MAX = 500; // liste bornée ; le CSV et les compteurs couvrent tous les codes
const SELECT = "SELECT code_hash, school, licence, expires_at, used_at FROM codes";

async function view(ctx: Ctx, message = ""): Promise<View | Response> {
  const { results } = await ctx.env.DB.prepare(`${SELECT} ORDER BY expires_at DESC`).all<Row>();
  if (ctx.url.pathname.endsWith(".csv")) return csv(results, ctx.now);

  const asked = ctx.url.searchParams.get("etat") ?? "";
  const etat = ["attente", "actives", "expires"].includes(asked) ? asked : "tous";
  const q = (ctx.url.searchParams.get("q") ?? "").trim().slice(0, 100);
  const count = (e: Etat) => results.filter((r) => stateOf(r, ctx.now) === e).length;
  const matching = results.filter((r) => (etat === "tous" || stateOf(r, ctx.now) === etat) && r.school.toLowerCase().includes(q.toLowerCase()));
  const shown = matching.slice(0, LIST_MAX);
  const hidden = matching.length - shown.length;

  // Pré-remplissage pour un renouvellement.
  const hash = ctx.url.searchParams.get("renouveler") ?? "";
  const old = HASH.test(hash) ? results.find((r) => r.code_hash === hash) : undefined;
  const renew = old ? renewal(claimsOf(old.licence)) : null;
  const v: LicenceRequest = renew ?? { organization: "", tier: "", kind: "subscription", starts: "", ends: "" };
  const renewNotice = renew ? notice(`Renouvellement de ${escape(renew.organization)} : vérifiez puis créez le code.`) : "";

  const link = (label: string, e: string) => `<a href="/licences?${new URLSearchParams({ ...(e !== "tous" ? { etat: e } : {}), ...(q ? { q } : {}) })}"${e === etat ? ' class="on"' : ""}>${label}</a>`;
  const rows = shown.map((row) => {
    const c = claimsOf(row.licence);
    const s = stateOf(row, ctx.now);
    const state = s === "actives" ? pill("ok", `Activé le ${day(row.used_at)}`)
      : s === "attente" ? pill("warn", `En attente · jusqu'au ${day(row.expires_at)}`) : pill("neutral", "Code expiré");
    const actions = `<a class="btn ghost small" href="/licences?renouveler=${row.code_hash}#nouveau">Renouveler</a>`
      + (s === "attente" ? ` <form method="post" action="/codes/revoke" class="inline"><input type="hidden" name="code_hash" value="${escape(row.code_hash)}"><button type="submit" class="small danger">Révoquer</button></form>` : "");
    return `<tr><td data-label="Établissement"><strong>${escape(row.school)}</strong></td><td data-label="Offre">${escape(c.tier)}</td><td data-label="Type">${escape(c.kind === "pilot" ? "Pilote" : "Abonnement")}</td><td data-label="Licence">${escape(c.starts)} → ${escape(c.ends)}</td><td data-label="Enveloppe d'IA">${c.ai_envelope_eur === undefined ? "—" : `${escape(c.ai_envelope_eur)} €`}</td><td data-label="État">${state}</td><td data-label="Actions">${actions}</td></tr>`;
  }).join("");
  const table = rows ? `<div class="scroll"><table class="cards"><thead><tr><th>Établissement</th><th>Offre</th><th>Type</th><th>Licence</th><th>Enveloppe d'IA</th><th>État</th><th>Actions</th></tr></thead><tbody>${rows}</tbody></table></div>`
    : empty(results.length ? "Aucun code ne correspond à ces filtres." : "Aucun code pour l'instant.");

  return {
    title: "Licences",
    notice: message || renewNotice,
    body: `<div class="kpis">${kpi("Codes en attente", String(count("attente")))}${kpi("Codes activés", String(count("actives")))}${kpi("Codes expirés sans usage", String(count("expires")))}</div>
<div class="grid"><article class="card" id="nouveau"><header><h3>Nouveau code d'activation</h3></header><p class="muted">À usage unique : la DSI le colle dans NOVA_ACTIVATION_CODE.</p>
<form method="post" action="/codes"><input type="hidden" name="nonce" value="${crypto.randomUUID()}">
<label>Établissement<input name="organization" required maxlength="100" value="${escape(v.organization)}"></label>
<label>Offre (tranche)<input name="tier" required maxlength="40" placeholder="151-250" value="${escape(v.tier)}"></label>
<label>Type<select name="kind"><option value="pilot"${v.kind === "pilot" ? " selected" : ""}>Pilote</option><option value="subscription"${v.kind !== "pilot" ? " selected" : ""}>Abonnement</option></select></label>
<label>Début<input name="starts" type="date" required value="${escape(v.starts)}"></label>
<label>Fin<input name="ends" type="date" required value="${escape(v.ends)}"></label>
<label>Enveloppe d'IA (€, facultatif)<input name="ai_envelope_eur" type="number" min="0" step="1" value="${v.ai_envelope_eur === undefined ? "" : escape(v.ai_envelope_eur)}"></label>
<label>Code valable (jours)<input name="code_days" type="number" min="1" max="90" value="30"></label>
<button type="submit">Créer le code</button></form></article>
<article class="card"><header><h3>Codes donnés</h3><a class="btn ghost small" href="/licences.csv">Exporter (CSV)</a></header>
<div class="toolbar"><div class="seg">${link("Tous", "tous")}${link("En attente", "attente")}${link("Activés", "actives")}${link("Expirés", "expires")}</div>
<form method="get" action="/licences" class="inline">${etat !== "tous" ? `<input type="hidden" name="etat" value="${escape(etat)}">` : ""}<input type="search" name="q" value="${escape(q)}" placeholder="Rechercher un établissement"><button type="submit" class="ghost small">Rechercher</button></form></div>
${hidden ? `<p class="muted">${hidden} codes de plus ne sont pas affichés : affinez la recherche ou exportez le CSV.</p>` : ""}${table}</article></div>`,
  };
}

async function create(ctx: Ctx, form: FormData): Promise<Response> {
  const text = (name: string) => String(form.get(name) ?? "");
  const envelope = text("ai_envelope_eur").trim();
  const r: LicenceRequest = {
    organization: text("organization"), tier: text("tier"), kind: text("kind"), starts: text("starts"), ends: text("ends"),
    ...(envelope ? { ai_envelope_eur: Number(envelope) } : {}),
  };
  const asked = Math.floor(Number(text("code_days").trim() || NaN));
  const days = Number.isFinite(asked) ? Math.min(90, Math.max(1, asked)) : 30; // vide ou illisible → 30 ; hors 1..90 → borné
  const show = async (message: string) => layout(ctx, "licences", (await view(ctx, message)) as View, {});
  const problem = invalid(r);
  if (problem) return show(notice(escape(problem), "bad"));
  if (!ctx.env.SIGNING_KEY) return show(notice("Clé de signature absente (secret SIGNING_KEY).", "bad"));
  const licence = await signLicence(ctx.env.SIGNING_KEY, r); // avant le jeton : une signature qui échoue ne le brûle pas
  // Recharger la page renvoie le même jeton : aucun second code.
  const nonce = text("nonce").slice(0, 64);
  if (nonce && !(await ctx.env.DB.prepare("INSERT OR IGNORE INTO form_nonces (nonce, at) VALUES (?, ?)").bind(nonce, ctx.now).run()).meta.changes)
    return show(notice("Ce code a déjà été créé ; il n'est pas affiché une seconde fois. S'il est perdu, révoquez-le et créez-en un autre."));
  const code = newCode();
  await ctx.env.DB.prepare("INSERT INTO codes (code_hash, licence, school, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256Hex(normalizeCode(code)), licence, r.organization.trim(), ctx.now + days * DAY)
    .run();
  await audit(ctx.env.DB, ctx.user, ctx.now, "Code créé", `${r.organization.trim()} · ${r.kind} · ${r.starts} → ${r.ends}`);
  return show(notice(`Code pour ${escape(r.organization.trim())}, valable ${plural(days, "jour", "jours")}, à donner à la DSI :<br><code class="big">${code}</code><br>Il ne sera plus affiché.`, "ok"));
}

async function revoke(ctx: Ctx, form: FormData): Promise<Response> {
  const hash = String(form.get("code_hash") ?? "");
  if (!HASH.test(hash)) return new Response("Code invalide", { status: 400 });
  const row = await ctx.env.DB.prepare(`${SELECT} WHERE code_hash = ?`).bind(hash).first<Row>();
  // Un code déjà utilisé ou expiré n'est jamais modifié.
  const done = row && (await ctx.env.DB.prepare("UPDATE codes SET expires_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?")
    .bind(ctx.now, hash, ctx.now).run()).meta.changes;
  await audit(ctx.env.DB, ctx.user, ctx.now, done ? "Code révoqué" : "Révocation sans effet", row?.school ?? "code inconnu");
  return redirect("/licences");
}

export const licences: Page = {
  id: "licences",
  path: "/licences",
  render: (ctx) => view(ctx),
  actions: { "/codes": create, "/codes/revoke": revoke },
  css: ".grid{grid-template-columns:minmax(0,1fr)}", // le tableau des codes a besoin de toute la largeur
};
