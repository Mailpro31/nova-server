import { ago, bars, escape, loadFleet, LICENCE_LABEL, LICENCE_TONE, num, plural, text, type Fleet, type SchoolData } from "../dashboard";
import { claimsOf } from "../licence";
import { empty, pill, stat, when, type Ctx, type Page, type View } from "../shell";

/** Flotte Grafana, ou la vue d'erreur à rendre à la place. Partagée avec Usage et Coûts d'IA. */
export async function fleetOrError(ctx: Ctx, title: string): Promise<{ fleet: Fleet } | { error: View }> {
  const r = await loadFleet(ctx.env, ctx.now);
  if (r.ok) return { fleet: r.fleet };
  const text = r.reason === "token" ? "Jeton de lecture Grafana absent (secret GRAFANA_READ_TOKEN)." : "Grafana Cloud ne répond pas : réessayez dans un instant.";
  return { error: { title, body: `${empty(text)}<p><a href="/etat">État des services</a></p>` } };
}

const href = (id: string) => `/ecoles/${encodeURIComponent(id)}`;
const state = (s: SchoolData, now: number) => (s.online ? pill("ok", "En ligne") : pill("warn", `Muet depuis ${ago(now - s.lastSeen)}`));
const licencePill = (s: SchoolData) => {
  const l = s.licence;
  if (!l) return pill("neutral", "Sans licence");
  const days = l.daysLeft === undefined ? "" : ` · ${num(l.daysLeft)} j`;
  return pill(LICENCE_TONE[l.status] ?? "neutral", `${LICENCE_LABEL[l.status] ?? l.status}${days}`);
};
const card = (title: string, inner: string) => `<article class="card"><header><h3>${escape(title)}</h3></header>${inner}</article>`;

function list(ctx: Ctx, fleet: Fleet): View {
  const vue = ctx.url.searchParams.get("vue");
  const filter = vue === "en-ligne" ? (s: SchoolData) => s.online : vue === "muettes" ? (s: SchoolData) => !s.online : () => true;
  const tab = (key: string, label: string, on: boolean) => `<a href="/ecoles${key ? `?vue=${key}` : ""}"${on ? ' class="on"' : ""}>${label}</a>`;
  const rows = fleet.schools.filter(filter).map((s) =>
    `<a class="row-link" href="${href(s.id)}"><div class="grow"><strong>${escape(s.id)}</strong><div class="chips" style="margin-top:4px;align-items:center">${state(s, ctx.now)} ${licencePill(s)}`
    + `<span class="muted">${plural(s.activeMonth, "élève actif", "élèves actifs")}</span>${s.serverVersion ? `<span class="chip">${escape(s.serverVersion)}</span>` : ""}</div></div><span class="chev">›</span></a>`).join("");
  return {
    title: "Écoles",
    body: `<div class="toolbar"><div class="seg">${tab("", "Toutes", vue !== "en-ligne" && vue !== "muettes")}${tab("en-ligne", "En ligne", vue === "en-ligne")}${tab("muettes", "Muettes", vue === "muettes")}</div></div>`
      + (rows ? `<div class="card flush">${rows}</div>` : empty("Aucune école dans cette vue.")),
  };
}

const known = (v: string) => /^\d+(\.\d+)*$/.test(v); // « other » : forme non reconnue
// Comparaison numérique des versions « 1.0.82 » ; shortcut: pas de suffixe de pré-version.
const newer = (a: string, b: string) => {
  const [x, y] = [a, b].map((v) => v.split(".").map(Number));
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) > (y[i] ?? 0);
  return false;
};

async function detail(ctx: Ctx, fleet: Fleet, raw: string): Promise<View> {
  let id = raw;
  try { id = decodeURIComponent(raw); } catch { /* identifiant laissé tel quel */ }
  const s = fleet.schools.find((x) => x.id === id);
  const back = `<p><a href="/ecoles">‹ Toutes les écoles</a></p>`;
  if (!s) return { title: "École inconnue", body: `${back}${empty("Aucune mesure ne remonte pour cette école.")}` };

  const latest = fleet.schools.flatMap((x) => x.apps.map((a) => a.version)).filter(known).reduce((m, v) => (newer(v, m) ? v : m), "0");
  const l = s.licence;
  const envelope = s.envelopeAnnual === undefined ? 0 : s.envelopeAnnual / 12;
  const part = envelope > 0 ? (s.spendMonth ?? 0) / envelope : 0;
  const tone = part <= 0.8 ? "ok" : part <= 1 ? "warn" : "bad";
  const reports = (fleet.reports ?? []).filter((r) => r.school === s.id).map((r) =>
    `<li><header>${pill("neutral", text(r.line.app_version ?? "?"))}<time>${escape(when(r.at))}</time></header><blockquote>${escape(r.line.description ?? "")}</blockquote></li>`).join("");
  const codes = (await ctx.env.DB.prepare("SELECT school, licence, expires_at, used_at FROM codes ORDER BY expires_at DESC")
    .all<{ school: string; licence: string; expires_at: number; used_at: number | null }>()).results
    .filter((c) => c.school.trim().toLowerCase() === s.id.trim().toLowerCase());
  const day = (t: number) => new Date(t * 1000).toISOString().slice(0, 10);
  const codeRows = codes.map((c) => {
    const claims = claimsOf(c.licence);
    const st = c.used_at ? pill("ok", `Activé le ${day(c.used_at)}`) : c.expires_at > ctx.now ? pill("warn", `En attente · jusqu'au ${day(c.expires_at)}`) : pill("neutral", "Expiré");
    return `<li><strong>${escape(claims.tier)}</strong> <span class="muted">${escape(claims.starts)} → ${escape(claims.ends)}</span> ${st}</li>`;
  }).join("");

  const body = `${back}<div class="stats">${stat("Comptes actifs", num(s.members))}${stat("Élèves actifs (mois)", num(s.activeMonth))}${stat("Dictées 24 h", num(s.dictations24h))}${stat("Jetons d'IA (30 j)", num(s.tokens30d))}</div>
<div class="grid">
${card("Licence", l ? `<dl><dt>Statut</dt><dd>${pill(LICENCE_TONE[l.status] ?? "neutral", LICENCE_LABEL[l.status] ?? l.status)}</dd><dt>Offre</dt><dd>${escape(l.tier)}</dd><dt>Jours restants</dt><dd>${num(l.daysLeft)}</dd></dl>` : empty("Aucune licence reçue."))}
${card("Postes", s.apps.length ? `<div class="chips">${s.apps.map((a) => `<span class="chip">${escape(a.version)} ×${num(a.count)}</span> ${!known(a.version) ? pill("neutral", "inconnue") : a.version === latest ? pill("ok", "à jour") : pill("warn", "ancienne")}`).join("")}</div>` : empty("Aucun poste connu."))}
${card("Coûts d'IA", envelope > 0 ? `<div class="meter ${tone}"><div style="width:${Math.min(100, part * 100).toFixed(1)}%"></div></div><p>${num(s.spendMonth, 2)} € sur ${num(envelope, 2)} € ce mois (${num(part * 100)} %).</p>` : `${empty("Pas d'enveloppe dans la licence")}<p>Dépensé ce mois : ${num(s.spendMonth, 2)} €.</p>`)}
${card("Codes d'activation", `${codes.length ? `<ul class="list">${codeRows}</ul>` : empty("Aucun code donné à ce nom.")}<p><a href="/licences">Licences</a></p>`)}
</div>
<section>${card("Usage", `<div class="charts"><figure><figcaption>Dictées par jour</figcaption>${bars(s.dictSeries, "dictées")}</figure><figure><figcaption>Temps de transcription (95 % des dictées, en s)</figcaption>${bars(s.latencySeries, "s")}</figure></div>`)}</section>
<section>${card("Signalements", `${reports ? `<ul class="list">${reports}</ul>` : empty("Aucun signalement récent.")}<p><a href="/signalements?ecole=${encodeURIComponent(s.id)}">Tous les signalements</a></p>`)}</section>
<p><a href="${escape(ctx.env.GRAFANA_URL)}/explore" target="_blank" rel="noopener">Voir dans Grafana ↗</a></p>`;
  return {
    title: s.id,
    subtitle: `${s.online ? "En ligne" : `Muet depuis ${escape(ago(ctx.now - s.lastSeen))}`} · dernier envoi ${escape(when(s.lastSeen))}`,
    body,
  };
}

export const ecoles: Page = {
  id: "ecoles",
  path: "/ecoles",
  async render(ctx) {
    const r = await fleetOrError(ctx, "Écoles");
    if ("error" in r) return r.error;
    const rest = ctx.url.pathname.slice("/ecoles/".length);
    return ctx.url.pathname.startsWith("/ecoles/") && rest ? detail(ctx, r.fleet, rest) : list(ctx, r.fleet);
  },
};
