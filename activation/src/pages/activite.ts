// Activité : un seul fil chronologique, actions faites dans la console (console_audit) et actions de Claude (pilot_journal).
import { escape } from "../dashboard";
import { auditDetail, empty, eventRow, pill, type Page } from "../shell";

const PER_PAGE = 50;
const TZ = "Europe/Paris";
const dayKey = (s: number) => new Date(s * 1000).toLocaleDateString("en-CA", { timeZone: TZ }); // AAAA-MM-JJ à Paris
const dayNumber = (key: string) => Date.UTC(+key.slice(0, 4), +key.slice(5, 7) - 1, +key.slice(8, 10)) / 86400000;

/** Intertitre d'un jour (heure de Paris) : « Aujourd'hui », « Hier », sinon la date en toutes lettres. Pure. */
export function dayLabel(at: number, now: number): string {
  const gap = dayNumber(dayKey(now)) - dayNumber(dayKey(at));
  if (gap === 0) return "Aujourd'hui";
  if (gap === 1) return "Hier";
  return new Date(at * 1000).toLocaleDateString("fr-FR", { timeZone: TZ, weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

const time = (s: number) => new Date(s * 1000).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const RESULT: Record<string, ["ok" | "bad" | "neutral", string]> = { ok: ["ok", "Réussi"], echec: ["bad", "Échec"], info: ["neutral", "Info"] };
const SOURCES: [string, string][] = [["tout", "Tout"], ["console", "Console"], ["claude", "Claude"]];

interface Row { src: string; at: number; who: string; title: string; detail: string | null; result: string | null; kind: string | null }

export const activite: Page = {
  id: "activite",
  path: "/activite",
  async render(ctx) {
    const asked = ctx.url.searchParams.get("source") ?? "";
    const source = SOURCES.some(([v]) => v === asked) ? asked : "tout";
    const page = Math.min(100000, Math.max(1, Math.floor(Number(ctx.url.searchParams.get("page"))) || 1));
    const { results } = await ctx.env.DB.prepare(
      `SELECT * FROM (
         SELECT 'console' AS src, id, at, by AS who, action AS title, detail, NULL AS result, NULL AS kind FROM console_audit
         UNION ALL
         SELECT 'claude', id, at, 'Claude', title, NULL, result, kind FROM pilot_journal)
       WHERE ? = 'tout' OR src = ? ORDER BY at DESC, id DESC LIMIT ? OFFSET ?`)
      .bind(source, source, PER_PAGE + 1, (page - 1) * PER_PAGE).all<Row>();
    const rows = results.slice(0, PER_PAGE);

    const href = (p: number, s = source) => `/activite?${new URLSearchParams({ ...(s !== "tout" ? { source: s } : {}), ...(p > 1 ? { page: String(p) } : {}) })}`;
    const filters = `<div class="toolbar"><nav class="seg" aria-label="Source">${SOURCES.map(([v, l]) => `<a href="${escape(href(1, v))}" class="${v === source ? "on" : ""}">${l}</a>`).join("")}</nav></div>`;

    let current = "", list = "";
    for (const r of rows) {
      const label = dayLabel(r.at, ctx.now);
      if (label !== current) { list += `${current ? "</div>" : ""}<h2>${escape(label)}</h2><div class="card flush">`; current = label; }
      const [tone, text] = RESULT[r.result ?? ""] ?? ["neutral", r.result ?? ""];
      const claude = r.src === "claude";
      list += eventRow(`${claude ? `<span class="chip">${escape(r.kind)}</span> ${pill(tone, text)} ` : ""}<strong>${escape(r.title)}</strong> <span class="muted">par ${escape(r.who)}${r.detail ? ` · ${escape(auditDetail(r.title, r.detail))}` : ""}${claude ? ` · <a href="/claude?vue=journal">voir le journal</a>` : ""}</span>`, time(r.at));
    }
    list = rows.length ? `${list}</div>` : empty("Aucune activité.");

    const pager = page > 1 || results.length > PER_PAGE
      ? `<div class="toolbar">${page > 1 ? `<a class="btn ghost small" href="${escape(href(page - 1))}">Plus récent</a>` : ""}<span class="muted">Page ${page}</span>${results.length > PER_PAGE ? `<a class="btn ghost small" href="${escape(href(page + 1))}">Plus ancien</a>` : ""}</div>`
      : "";
    return { title: "Activité", subtitle: "Actions faites dans la console et par Claude, de la plus récente à la plus ancienne.", body: `${filters}${list}${pager}` };
  },
  css: `.main h2::first-letter{text-transform:uppercase}.main h2{margin-top:22px}`,
};
