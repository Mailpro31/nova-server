import { reportKey } from "./signalements";
import { ago, escape, loadFleet, num, LICENCE_LABEL, type FleetResult, type Report } from "../dashboard";
import { empty, eventRow, kpi, pill, when, type Ctx, type Page } from "../shell";

type Tone = "bad" | "warn" | "accent" | "neutral";
export interface Todo { tone: Tone; title: string; detail: string; href: string }
export interface TodoInput {
  fleet: FleetResult;
  now: number;
  openQuestions: number;
  paused: boolean;
  pending: { school: string; expires_at: number }[]; // codes non utilisés et non expirés
  treated: Set<string>;                              // clés de report_status
}

const DAY = 86400;
const ORDER: Record<Tone, number> = { bad: 0, warn: 1, accent: 2, neutral: 3 };
const LABEL: Record<Tone, string> = { bad: "Urgent", warn: "À surveiller", accent: "À trancher", neutral: "Info" };
const plural = (n: number, one: string, many: string) => `${n} ${n > 1 ? many : one}`;
const school = (id: string) => `/ecoles/${encodeURIComponent(id)}`;
const day = (s: number) => new Date(s * 1000).toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", day: "numeric", month: "long", year: "numeric" });
const parts = (v: string) => v.split(".").map((n) => parseInt(n, 10) || 0);
const cmp = (a: string, b: string) => {
  const x = parts(a), y = parts(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) - (y[i] ?? 0);
  return 0;
};


/** Signalements des 7 derniers jours pas encore traités. */
export const untreated = (reports: Report[] | null, treated: Set<string>, now: number) =>
  (reports ?? []).filter((r) => r.at > now - 7 * DAY && !treated.has(reportKey(r)));

/** Liste « À faire », triée par gravité. Pure : aucune lecture réseau ni base. */
export function todo(i: TodoInput): Todo[] {
  const out: Todo[] = [];
  if (!i.fleet.ok) {
    out.push({ tone: "bad", title: "Grafana Cloud ne répond pas",
      detail: i.fleet.reason === "token" ? "jeton de lecture Grafana absent" : "les mesures des écoles sont illisibles", href: "/etat" });
  } else {
    const { schools, reports } = i.fleet.fleet;
    for (const s of schools) {
      const to = school(s.id);
      if (!s.online) out.push({ tone: "bad", title: `${s.id} ne répond plus`, detail: `dernier envoi il y a ${ago(i.now - s.lastSeen)}`, href: to });
      const l = s.licence;
      if (l) {
        if (l.status === "expired" || l.status === "invalid") out.push({ tone: "bad", title: `Licence de ${s.id} : ${(LICENCE_LABEL[l.status] ?? l.status).toLowerCase()}`, detail: "à renouveler", href: to });
        else if (l.status === "grace") out.push({ tone: "bad", title: `Licence de ${s.id} en délai de grâce`, detail: "à renouveler", href: to });
        else if (l.status === "warning" || (l.daysLeft !== undefined && l.daysLeft <= 30)) {
          out.push({ tone: "warn", title: `Licence de ${s.id} : ${l.daysLeft !== undefined ? `${num(l.daysLeft)} j restants` : "bientôt échue"}`, detail: "à renouveler", href: to });
        }
      }
      if (s.envelopeAnnual && s.spendMonth !== undefined) {
        const ratio = s.spendMonth / (s.envelopeAnnual / 12);
        if (ratio > 0.8) out.push({ tone: ratio > 1 ? "bad" : "warn", title: `Enveloppe d'IA de ${s.id} : ${Math.round(ratio * 100)} % du mois`,
          detail: `${num(s.spendMonth, 2)} € dépensés sur ${num(s.envelopeAnnual / 12, 2)} € par mois`, href: "/ia" });
      }
    }
    const n = untreated(reports, i.treated, i.now).length;
    if (n) out.push({ tone: "warn", title: plural(n, "signalement à traiter", "signalements à traiter"), detail: "reçus ces 7 derniers jours", href: "/signalements" });
    const latest = schools.flatMap((s) => s.apps.map((a) => a.version)).sort(cmp).pop();
    if (latest) for (const s of schools) {
      const old = s.apps.filter((a) => cmp(a.version, latest) < 0).reduce((t, a) => t + a.count, 0);
      if (old) out.push({ tone: "neutral", title: `${s.id} : ${plural(old, "poste sur une ancienne version", "postes sur une ancienne version")}`, detail: `dernière version : ${latest}`, href: school(s.id) });
    }
  }
  if (i.openQuestions) out.push({ tone: "accent", title: `Claude attend ${plural(i.openQuestions, "réponse", "réponses")}`, detail: "questions ouvertes", href: "/claude" });
  if (i.paused) out.push({ tone: "neutral", title: "Claude est en pause", detail: "aucune action automatique", href: "/claude" });
  for (const c of i.pending) {
    if (c.expires_at - i.now < 7 * DAY) out.push({ tone: "warn", title: `Code pour ${c.school} : expire le ${day(c.expires_at)}`, detail: "code d'activation pas encore utilisé", href: "/licences" });
  }
  return out.sort((a, b) => ORDER[a.tone] - ORDER[b.tone]);
}

const rows = async <T>(ctx: Ctx, sql: string, ...args: unknown[]): Promise<T[]> => {
  try { return (await ctx.env.DB.prepare(sql).bind(...args).all<T>()).results; } catch { return []; }
};

async function render(ctx: Ctx) {
  const [fleet, questions, paused, pending, statuses, audits, journal] = await Promise.all([
    loadFleet(ctx.env, ctx.now),
    rows<{ n: number }>(ctx, "SELECT COUNT(*) AS n FROM pilot_questions WHERE answer IS NULL"),
    rows<{ value: string }>(ctx, "SELECT value FROM pilot_state WHERE key = 'paused'"),
    rows<{ school: string; expires_at: number }>(ctx, "SELECT school, expires_at FROM codes WHERE used_at IS NULL AND expires_at > ? ORDER BY expires_at", ctx.now),
    rows<{ report_key: string }>(ctx, "SELECT report_key FROM report_status"),
    rows<{ at: number; by: string; action: string; detail: string | null }>(ctx, "SELECT at, by, action, detail FROM console_audit ORDER BY at DESC LIMIT 5"),
    rows<{ at: number; kind: string; result: string; title: string }>(ctx, "SELECT at, kind, result, title FROM pilot_journal ORDER BY at DESC LIMIT 5"),
  ]);
  const treated = new Set(statuses.map((r) => r.report_key));
  const items = todo({ fleet, now: ctx.now, openQuestions: questions[0]?.n ?? 0, paused: paused[0]?.value === "1", pending, treated });

  const f = fleet.ok ? fleet.fleet : null;
  const sum = (pick: (s: NonNullable<typeof f>["schools"][number]) => number | undefined) =>
    f ? num(f.schools.reduce((t, s) => t + (pick(s) ?? 0), 0)) : "—";
  const online = f ? f.schools.filter((s) => s.online).length : 0;
  const spend = f ? f.schools.reduce((t, s) => t + (s.spendMonth ?? 0), 0) : 0;
  const kpis = `<div class="kpis">
${kpi("Écoles en ligne", f ? `${online} <small>/ ${f.schools.length}</small>` : "—", "", "/ecoles")}
${kpi("Élèves actifs ce mois", sum((s) => s.activeMonth), "", "/usage")}
${kpi("Dictées sur 24 h", sum((s) => s.dictations24h), "", "/usage")}
${kpi("Dépense d'IA du mois", f ? `${num(spend, 2)} <small>€</small>` : "—", "", "/ia")}
${kpi("Signalements à traiter", f ? num(untreated(f.reports, treated, ctx.now).length) : "—", "", "/signalements")}
</div>`;

  const list = items.length
    ? `<div class="card flush">${items.map((t) => `<a class="row-link" href="${escape(t.href)}">${pill(t.tone, LABEL[t.tone])}<div class="grow"><strong>${escape(t.title)}</strong><div class="muted">${escape(t.detail)}</div></div><span class="chev">›</span></a>`).join("")}</div>`
    : `<div class="empty">${pill("ok", "OK")} Rien à faire : tout va bien.</div>`;

  const events = [
    ...audits.map((a) => ({ at: a.at, html: `<strong>${escape(a.action)}</strong> <span class="muted">par ${escape(a.by)}${a.detail ? ` · ${escape(a.detail)}` : ""}</span>` })),
    ...journal.map((j) => ({ at: j.at, html: `${pill(j.result === "ok" ? "ok" : j.result === "echec" ? "bad" : "neutral", j.result)} <strong>${escape(j.title)}</strong> <span class="muted">par Claude</span>` })),
  ].sort((a, b) => b.at - a.at).slice(0, 5);
  const activity = events.length
    ? `<div class="card flush">${events.map((e) => eventRow(e.html, when(e.at))).join("")}</div>`
    : empty("Aucune activité pour l'instant.");

  return {
    title: "Accueil",
    subtitle: escape(new Date(ctx.now * 1000).toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", weekday: "long", day: "numeric", month: "long", year: "numeric" })),
    body: `${kpis}<section><h2>À faire</h2>${list}</section>
<section><h2>Dernière activité <small><a href="/activite">Tout voir</a></small></h2>${activity}</section>`,
  };
}

export const accueil: Page = { id: "accueil", path: "/", render };
