import { escape, loadFleet, text, type Report } from "../dashboard";
import { audit, empty, kpi, pill, redirect, when, type Ctx, type Page } from "../shell";

/** Clé d'un signalement dans report_status : école + horodatage en millisecondes. */
export const reportKey = (r: Report) => `${r.school}:${Math.round(r.at * 1000)}`;

interface Status { report_key: string; status: string; note: string | null; at: number; by: string }
const VUES = ["a-traiter", "traites", "tous"];
const LABEL: Record<string, string> = { traite: "Traité", ignore: "Ignoré" };
const AUDIT: Record<string, string> = { traite: "Signalement traité", ignore: "Signalement ignoré", rouvrir: "Signalement rouvert" };

const href = (vue: string, ecole: string) => `/signalements?${new URLSearchParams({ vue, ...(ecole ? { ecole } : {}) })}`;

async function render(ctx: Ctx) {
  const fleet = await loadFleet(ctx.env, ctx.now);
  if (!fleet.ok) return { title: "Signalements", body: `${empty("Grafana Cloud ne répond pas.")}<p><a href="/etat">Voir l'état des services</a></p>` };
  const reports = fleet.fleet.reports;
  if (!reports) return { title: "Signalements", body: empty("Journaux illisibles : ajouter le droit logs:read au jeton de lecture Grafana.") };

  const { results } = await ctx.env.DB.prepare("SELECT report_key, status, note, at, by FROM report_status").all<Status>();
  const status = new Map(results.map((s) => [s.report_key, s]));
  const asked = ctx.url.searchParams.get("vue") ?? "";
  const vue = VUES.includes(asked) ? asked : "a-traiter";
  const ecole = ctx.url.searchParams.get("ecole") ?? "";
  const schools = [...new Set(reports.map((r) => r.school))].sort();
  const pending = reports.filter((r) => !status.has(reportKey(r)));

  const shown = reports.filter((r) => (!ecole || r.school === ecole)
    && (vue === "tous" || (vue === "traites") === status.has(reportKey(r))));
  const on = (cond: boolean) => (cond ? ' class="on"' : "");
  const button = (value: string, label: string, ghost = "") => `<button type="submit" name="statut" value="${value}" class="small ${ghost}">${label}</button>`;
  const cards = shown.map((r) => {
    const key = reportKey(r), st = status.get(key);
    const l = r.line;
    const engines = Object.entries((l.engines ?? {}) as Record<string, unknown>).filter(([, s]) => s !== "ok")
      .map(([name, s]) => pill(s === "error" ? "bad" : "warn", `${name} : ${text(s)}`));
    const closed = st
      ? `<p>${pill(st.status === "traite" ? "ok" : "neutral", `${LABEL[st.status] ?? st.status} par ${st.by} le ${when(st.at)}`)}${st.note ? ` <span class="muted">${escape(st.note)}</span>` : ""}</p>`
      : "";
    return `<li><article class="card"><header><strong><a href="/ecoles/${encodeURIComponent(r.school)}">${escape(r.school)}</a></strong>${l.app_version ? `<span class="chip">Nova ${escape(l.app_version)}</span>` : ""}<time>${when(r.at)}</time></header>
<blockquote>${l.description ? escape(l.description) : "Sans description."}</blockquote>
<div class="chips">${engines.length ? engines.join("") : pill("ok", "Tout va bien")}${l.last_error ? pill("bad", text(l.last_error)) : ""}</div>
${closed}
<form method="post" action="/signalements/statut" class="inline"><input type="hidden" name="report_key" value="${escape(key)}"><input type="hidden" name="vue" value="${escape(vue)}"><input type="hidden" name="ecole" value="${escape(ecole)}">
<input name="note" maxlength="300" placeholder="Note (facultatif)">${st ? button("rouvrir", "Rouvrir", "ghost") : button("traite", "Traité") + button("ignore", "Ignorer", "ghost")}</form></article></li>`;
  }).join("");

  return {
    title: "Signalements",
    subtitle: "Signalements envoyés depuis les postes, sur les 30 derniers jours.",
    body: `<div class="kpis">${kpi("À traiter", String(pending.length))}${kpi("Traités sur 30 j", String(reports.length - pending.length))}${kpi("Écoles concernées", String(schools.length))}</div>
<div class="toolbar"><div class="seg"><a href="${href("a-traiter", ecole)}"${on(vue === "a-traiter")}>À traiter</a><a href="${href("traites", ecole)}"${on(vue === "traites")}>Traités</a><a href="${href("tous", ecole)}"${on(vue === "tous")}>Tous</a></div>
<div class="seg"><a href="${href(vue, "")}"${on(!ecole)}>Toutes les écoles</a>${schools.map((s) => `<a href="${href(vue, s)}"${on(s === ecole)}>${escape(s)}</a>`).join("")}</div></div>
${cards ? `<ul class="list">${cards}</ul>` : empty("Aucun signalement dans cette vue.")}`,
  };
}

async function setStatus(ctx: Ctx, form: FormData): Promise<Response> {
  const key = String(form.get("report_key") ?? "");
  const statut = String(form.get("statut") ?? "");
  if (key.length > 200 || !/^.+:\d+$/.test(key) || !AUDIT[statut]) return new Response("Requête invalide", { status: 400 });
  const note = String(form.get("note") ?? "").trim().slice(0, 300);
  if (statut === "rouvrir") await ctx.env.DB.prepare("DELETE FROM report_status WHERE report_key = ?").bind(key).run();
  else await ctx.env.DB.prepare("INSERT OR REPLACE INTO report_status (report_key, status, note, at, by) VALUES (?, ?, ?, ?, ?)").bind(key, statut, note || null, ctx.now, ctx.user).run();
  await audit(ctx.env.DB, ctx.user, ctx.now, AUDIT[statut], key);
  const vue = String(form.get("vue") ?? ""), ecole = String(form.get("ecole") ?? "");
  return redirect(vue || ecole ? href(VUES.includes(vue) ? vue : "a-traiter", ecole) : "/signalements");
}

export const signalements: Page = { id: "signalements", path: "/signalements", render, actions: { "/signalements/statut": setStatus } };
