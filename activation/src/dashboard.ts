// Tableau de bord de la console : lit Grafana Cloud (mesures et journaux que
// chaque serveur d'école y envoie déjà) et rend du HTML sans script.

export interface MetricsEnv {
  PROM_URL: string; // https://prometheus-…grafana.net/api/prom
  PROM_USER: string;
  LOKI_URL: string; // https://logs-…grafana.net
  LOKI_USER: string;
  GRAFANA_READ_TOKEN: string; // secret : jeton de lecture (métriques, et journaux s'il le permet)
}

type Sample = { metric: Record<string, string>; value: [number, string] };
type Series = { metric: Record<string, string>; values: [number, string][] };

const DAY = 86400;
export const escape = (text: unknown) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

async function get<T>(url: string, user: string, token: string): Promise<T | null> {
  try {
    const response = await fetch(url, { headers: { authorization: `Basic ${btoa(`${user}:${token}`)}` } });
    if (!response.ok) return null;
    return ((await response.json()) as { data: T }).data;
  } catch {
    return null;
  }
}

const instant = (env: MetricsEnv, query: string) =>
  get<{ result: Sample[] }>(`${env.PROM_URL}/api/v1/query?query=${encodeURIComponent(query)}`, env.PROM_USER, env.GRAFANA_READ_TOKEN)
    .then((d) => d?.result ?? null);

const range = (env: MetricsEnv, query: string, now: number, days: number) =>
  get<{ result: Series[] }>(
    `${env.PROM_URL}/api/v1/query_range?query=${encodeURIComponent(query)}&start=${now - days * DAY}&end=${now}&step=${DAY}`,
    env.PROM_USER, env.GRAFANA_READ_TOKEN,
  ).then((d) => d?.result ?? null);

/** { école: valeur } pour une requête groupée par école. */
const bySchool = (samples: Sample[] | null) =>
  Object.fromEntries((samples ?? []).map((s) => [s.metric.school, Number(s.value[1])]));

const num = (value: number | undefined, digits = 0) =>
  value === undefined || Number.isNaN(value) ? "—" : value.toLocaleString("fr-FR", { maximumFractionDigits: digits });

const ago = (seconds: number) =>
  seconds < 3600 ? `${Math.round(seconds / 60)} min` : seconds < 2 * DAY ? `${Math.round(seconds / 3600)} h` : `${Math.round(seconds / DAY)} j`;

const LICENCE_LABEL: Record<string, string> = {
  valid: "Valide", warning: "Bientôt échue", grace: "Délai de grâce", expired: "Échue", none: "Aucune", invalid: "Invalide",
};

// ------------------------------------------------------------ graphiques

const dayName = (t: number, long: boolean) => new Date(t * 1000).toLocaleDateString("fr-FR",
  long ? { timeZone: "Europe/Paris", weekday: "long", day: "numeric", month: "long" } : { timeZone: "Europe/Paris", day: "numeric", month: "short" });

/** Barres journalières en HTML : survol ou toucher d'une barre → jour et valeur exacts (CSS seul, la CSP interdit le JS). */
export function bars(points: [number, number][], unit: string): string {
  if (!points.length) return `<p class="muted">Pas encore de mesure.</p>`;
  const max = Math.max(...points.map(([, v]) => v), 0) || 1;
  const value = (v: number) => `${num(v, unit === "s" ? 2 : 0)} ${unit}`;
  const cols = points.map(([t, v], i) => {
    const side = i < points.length / 4 ? " l" : i >= (points.length * 3) / 4 ? " r" : "";
    const text = `${dayName(t, true)} : ${value(v)}`;
    return `<button type="button" class="col${side}" aria-label="${escape(text)}"><i style="height:${Math.max(v > 0 ? 2 : 0, (v / max) * 100).toFixed(1)}%"></i>`
      + `<span class="tip">${escape(dayName(t, true))}<b>${escape(value(v))}</b></span></button>`;
  }).join("");
  return `<div class="bars"><div class="axis">max ${escape(value(max))}</div><div class="cols">${cols}</div>
<div class="axis ends"><span>${escape(dayName(points[0][0], false))}</span><span>${escape(dayName(points[points.length - 1][0], false))}</span></div></div>`;
}

const points = (series: Series[] | null, school: string): [number, number][] =>
  (series?.find((s) => s.metric.school === school)?.values ?? [])
    .map(([t, v]) => [t, Number(v)] as [number, number])
    .filter(([, v]) => Number.isFinite(v));

// ------------------------------------------------------------ signalements

export interface Report { at: number; school: string; line: Record<string, unknown> }

async function reports(env: MetricsEnv, now: number): Promise<Report[] | null> {
  const query = '{school=~".+"} |= "support.report"';
  const data = await get<{ result: { stream: Record<string, string>; values: [string, string][] }[] }>(
    `${env.LOKI_URL}/loki/api/v1/query_range?query=${encodeURIComponent(query)}&start=${(now - 30 * DAY) * 1e9}&end=${now * 1e9}&limit=50&direction=backward`,
    env.LOKI_USER, env.GRAFANA_READ_TOKEN,
  );
  if (!data) return null;
  const out: Report[] = [];
  const seen = new Set<string>(); // la même ligne arrive parfois par deux flux
  for (const stream of data.result) {
    for (const [ns, raw] of stream.values) {
      if (seen.has(ns + raw)) continue;
      seen.add(ns + raw);
      try {
        const line = JSON.parse(raw) as Record<string, unknown>;
        if (line.event === "support.report") out.push({ at: Number(ns) / 1e9, school: stream.stream.school, line });
      } catch { /* ligne non JSON : ignorée */ }
    }
  }
  return out.sort((a, b) => b.at - a.at);
}

// ------------------------------------------------------------ sections

type Tone = "ok" | "warn" | "bad" | "neutral";
const pill = (tone: Tone, text: string) => `<span class="pill ${tone}">${text}</span>`;
const LICENCE_TONE: Record<string, Tone> = {
  valid: "ok", warning: "warn", grace: "warn", expired: "bad", none: "neutral", invalid: "bad",
};
const kpi = (label: string, value: string, hint = "") =>
  `<div class="kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div>${hint ? `<div class="kpi-hint">${hint}</div>` : ""}</div>`;
const stat = (label: string, value: string) => `<div class="stat"><span>${label}</span><strong>${value}</strong></div>`;
const total = (values: Record<string, number>) => Object.values(values).reduce((x, y) => x + y, 0);

export async function dashboard(env: MetricsEnv, now: number): Promise<string> {
  if (!env.GRAFANA_READ_TOKEN) {
    return `<section id="ecoles"><h2>Écoles</h2><p class="empty">Jeton de lecture Grafana absent (secret GRAFANA_READ_TOKEN).</p></section>`;
  }
  const [seen, server, apps, members, active, dictations, licence, days, spend, envelope, tokens, dictSeries, latency, support] =
    await Promise.all([
      instant(env, "max by (school) (timestamp(nova_uptime_seconds))"),
      instant(env, "max by (school, server_version) (nova_server_info)"),
      instant(env, "max by (school, version) (nova_app_versions)"),
      instant(env, 'max by (school) (nova_members{state="active"})'),
      instant(env, 'max by (school) (nova_active_users{period="month"})'),
      instant(env, 'sum by (school) (increase(nova_events_total{event="inference_transcribe"}[1d]))'),
      instant(env, "max by (school, status, tier) (nova_licence_info)"),
      instant(env, "max by (school) (nova_licence_days_left)"),
      instant(env, "max by (school) (nova_ai_spend_eur_month)"),
      instant(env, "max by (school) (nova_ai_envelope_eur)"),
      instant(env, "sum by (school) (increase(nova_ai_tokens_total[30d]))"),
      range(env, 'sum by (school) (increase(nova_events_total{event="inference_transcribe"}[1d]))', now, 14),
      range(env, 'histogram_quantile(0.95, sum by (school, le) (rate(nova_stage_seconds_bucket{stage="transcription"}[1d])))', now, 14),
      reports(env, now),
    ]);
  if (!seen) return `<section id="ecoles"><h2>Écoles</h2><p class="empty">Grafana Cloud ne répond pas : réessayez dans un instant.</p></section>`;

  const last = bySchool(seen);
  const schools = Object.keys(last).sort();
  const m = bySchool(members), a = bySchool(active), d = bySchool(dictations), dl = bySchool(days);
  const sp = bySchool(spend), en = bySchool(envelope), tk = bySchool(tokens);
  const online = schools.filter((s) => now - last[s] < 600).length;
  const week = (support ?? []).filter((r) => now - r.at < 7 * DAY).length;

  const overview = `<div class="kpis">
${kpi("Écoles en ligne", `${online}<small> / ${schools.length}</small>`, online < schools.length ? pill("bad", "Une école ne répond plus") : pill("ok", "Tout répond"))}
${kpi("Élèves actifs ce mois", num(total(a)))}
${kpi("Dictées sur 24 h", num(total(d)))}
${kpi("Dépense d'IA du mois", `${num(total(sp), 2)} €`)}
${kpi("Signalements sur 7 j", support === null ? "—" : num(week), week ? pill("warn", "À lire plus bas") : "")}
</div>`;

  const cards = schools.map((school) => {
    const silent = now - last[school];
    const status = silent < 600 ? pill("ok", "En ligne") : pill("bad", `Muet depuis ${ago(silent)}`);
    const version = server?.find((s) => s.metric.school === school)?.metric.server_version ?? "—";
    const posts = (apps ?? []).filter((s) => s.metric.school === school && Number(s.value[1]) > 0)
      .map((s) => `<span class="chip">${escape(s.metric.version)} ×${num(Number(s.value[1]))}</span>`).join(" ") || "—";
    const lic = licence?.find((s) => s.metric.school === school)?.metric;
    const licText = lic
      ? pill(LICENCE_TONE[lic.status] ?? "neutral", `${LICENCE_LABEL[lic.status] ?? escape(lic.status)}${lic.tier !== "none" ? ` · ${escape(lic.tier)}` : ""}${dl[school] !== undefined ? ` · ${num(dl[school])} j` : ""}`)
      : "—";
    return `<article class="card"><header><h3>${escape(school)}</h3>${status}</header>
<div class="stats">${stat("Comptes actifs", num(m[school]))}${stat("Élèves actifs (mois)", num(a[school]))}${stat("Dictées 24 h", num(d[school]))}</div>
<dl><dt>Licence</dt><dd>${licText}</dd><dt>Serveur</dt><dd>${escape(version)}</dd><dt>Postes</dt><dd>${posts}</dd></dl></article>`;
  }).join("");

  const usage = schools.map((school) => `<article class="card"><header><h3>${escape(school)}</h3></header><div class="charts">
<figure><figcaption>Dictées par jour</figcaption>${bars(points(dictSeries, school), "dictées")}</figure>
<figure><figcaption>Temps de transcription (95 % des dictées, en s)</figcaption>${bars(points(latency, school), "s")}</figure></div></article>`).join("");

  const ai = schools.map((school) => {
    const month = en[school] !== undefined ? en[school] / 12 : undefined;
    const ratio = month ? (sp[school] ?? 0) / month : undefined;
    const tone: Tone = ratio === undefined ? "neutral" : ratio > 1 ? "bad" : ratio > 0.8 ? "warn" : "ok";
    const meter = ratio === undefined ? `<p class="muted">Pas d'enveloppe dans la licence.</p>`
      : `<div class="meter ${tone}"><div style="width:${Math.min(100, ratio * 100).toFixed(0)}%"></div></div>`;
    return `<article class="card"><header><h3>${escape(school)}</h3>${ratio === undefined ? "" : pill(tone, `${num(ratio * 100)} %`)}</header>${meter}
<div class="stats">${stat("Dépensé ce mois", `${num(sp[school], 2)} €`)}${stat("Part du mois", month ? `${num(month, 2)} €` : "—")}${stat("Enveloppe annuelle", en[school] !== undefined ? `${num(en[school])} €` : "Non fixée")}${stat("Jetons (30 j)", num(tk[school]))}</div></article>`;
  }).join("");

  const supportList = support === null
    ? `<p class="empty">Les journaux ne sont pas lisibles avec ce jeton : ajoutez le droit <em>logs:read</em> à sa politique d'accès Grafana Cloud.</p>`
    : support.length === 0
      ? `<p class="empty">Aucun signalement depuis 30 jours.</p>`
      : `<ul class="reports">${support.map((r) => {
        const engines = Object.entries((r.line.engines ?? {}) as Record<string, string>).filter(([, v]) => v !== "ok");
        const chips = engines.length
          ? engines.map(([k, v]) => pill(v === "error" ? "bad" : "warn", `${escape(k)} : ${escape(v)}`)).join(" ")
          : pill("ok", "Tout va bien");
        const when = new Date(r.at * 1000).toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "medium", timeStyle: "short" });
        const description = String(r.line.description ?? "").trim();
        return `<li class="card"><header><strong>${escape(r.school)}</strong><span class="chip">Nova ${escape(r.line.app_version)}</span><time>${escape(when)}</time></header>
${description ? `<blockquote>${escape(description)}</blockquote>` : `<p class="muted">Sans description.</p>`}
<div class="chips">${chips}${r.line.last_error ? ` ${pill("bad", `Erreur : ${escape(r.line.last_error)}`)}` : ""}</div></li>`;
      }).join("")}</ul>`;

  return `<section id="apercu">${overview}</section>
<section id="ecoles"><h2>Écoles</h2><div class="grid">${cards || `<p class="empty">Aucune école ne remonte de mesures.</p>`}</div></section>
<section id="usage"><h2>Usage <small>14 derniers jours</small></h2><div class="stack">${usage}</div></section>
<section id="ia"><h2>Coûts d'IA</h2><p class="lead">Dépense du mois comparée au douzième de l'enveloppe annuelle.</p><div class="grid">${ai}</div></section>
<section id="signalements"><h2>Signalements <small>30 derniers jours</small></h2>${supportList}</section>`;
}
