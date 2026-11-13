import { escape, num } from "../dashboard";
import { kpi, pill, type Page } from "../shell";
import { fleetOrError } from "./ecoles";

/** Dépense de fin de mois : dépense × jours du mois / jour courant, en heure de Paris. */
export function projection(spent: number, now: number): number {
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Paris", year: "numeric", month: "numeric", day: "numeric" })
    .formatToParts(new Date(now * 1000)).map((x) => [x.type, Number(x.value)]));
  return (spent * new Date(Date.UTC(p.year, p.month, 0)).getUTCDate()) / p.day;
}

const eur = (v: number | undefined) => `${num(v, 2)} €`;

export const ia: Page = {
  id: "ia",
  path: "/ia",
  async render(ctx) {
    const r = await fleetOrError(ctx, "Coûts d'IA");
    if ("error" in r) return r.error;
    const { schools } = r.fleet;
    const spent = schools.reduce((a, s) => a + (s.spendMonth ?? 0), 0);
    const envelopes = schools.reduce((a, s) => a + (s.envelopeAnnual ?? 0), 0);
    const tokens = schools.reduce((a, s) => a + (s.tokens30d ?? 0), 0);
    const rows = schools.map((s) => {
      const share = s.envelopeAnnual ? s.envelopeAnnual / 12 : 0;
      const part = share > 0 ? (s.spendMonth ?? 0) / share : undefined;
      const tone = part === undefined ? "neutral" : part <= 0.8 ? "ok" : part <= 1 ? "warn" : "bad";
      const used = part === undefined ? pill("neutral", "Pas d'enveloppe")
        : `<div class="meter ${tone}"><div style="width:${Math.min(100, part * 100).toFixed(1)}%"></div></div>${pill(tone, `${num(part * 100)} %`)}`;
      return `<tr><td data-label="École"><a href="/ecoles/${encodeURIComponent(s.id)}"><strong>${escape(s.id)}</strong></a></td>`
        + `<td data-label="Dépensé ce mois">${eur(s.spendMonth)}</td><td data-label="Part du mois">${share > 0 ? eur(share) : "—"}</td>`
        + `<td data-label="Consommé">${used}</td><td data-label="Projection">${eur(projection(s.spendMonth ?? 0, ctx.now))}</td>`
        + `<td data-label="Jetons (30 j)">${num(s.tokens30d)}</td></tr>`;
    }).join("");
    return {
      title: "Coûts d'IA",
      body: `<div class="kpis">${kpi("Dépense du mois", eur(spent))}${kpi("Enveloppes annuelles", eur(envelopes))}${kpi("Projection fin de mois", eur(projection(spent, ctx.now)))}${kpi("Jetons sur 30 j", num(tokens))}</div>
<p class="lead">Dépense du mois comparée au douzième de l'enveloppe annuelle.</p>
<div class="card"><div class="scroll"><table class="cards"><thead><tr><th>École</th><th>Dépensé ce mois</th><th>Part du mois</th><th>Consommé</th><th>Projection</th><th>Jetons (30 j)</th></tr></thead><tbody>${rows || `<tr><td colspan="6" class="muted">Aucune école.</td></tr>`}</tbody></table></div></div>`,
    };
  },
};
