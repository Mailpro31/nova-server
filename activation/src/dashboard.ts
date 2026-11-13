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

/** Barres journalières en SVG, avec la valeur du dernier jour et le maximum. */
export function bars(points: [number, number][], unit: string): string {
  if (!points.length) return `<p class="muted">Pas encore de mesure.</p>`;
  const w = 560, h = 120, gap = 4, max = Math.max(...points.map(([, v]) => v), 0) || 1;
  const bw = (w - gap * (points.length - 1)) / points.length;
  const rects = points.map(([t, v], i) => {
    const bh = Math.max(v > 0 ? 2 : 0, (v / max) * (h - 18));
    const label = `${new Date(t * 1000).toISOString().slice(5, 10)} : ${num(v, 2)} ${unit}`;
    return `<rect x="${(i * (bw + gap)).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="2"><title>${escape(label)}</title></rect>`;
  }).join("");
  const first = new Date(points[0][0] * 1000).toISOString().slice(5, 10);
  const last = new Date(points[points.length - 1][0] * 1000).toISOString().slice(5, 10);
  return `<svg class="chart" viewBox="0 0 ${w} ${h + 16}" role="img" aria-label="${escape(unit)} par jour">
<text x="0" y="10" class="axis">max ${escape(num(max, 2))} ${escape(unit)}</text>${rects}
<text x="0" y="${h + 14}" class="axis">${first}</text><text x="${w}" y="${h + 14}" class="axis" text-anchor="end">${last}</text></svg>`;
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

export async function dashboard(env: MetricsEnv, now: number): Promise<string> {
  if (!env.GRAFANA_READ_TOKEN) {
    return `<section><h2>Écoles</h2><p>Jeton de lecture Grafana absent (secret GRAFANA_READ_TOKEN).</p></section>`;
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
  if (!seen) return `<section><h2>Écoles</h2><p>Grafana Cloud ne répond pas : réessayez dans un instant.</p></section>`;

  const last = bySchool(seen);
  const schools = Object.keys(last).sort();
  const m = bySchool(members), a = bySchool(active), d = bySchool(dictations), dl = bySchool(days);
  const sp = bySchool(spend), en = bySchool(envelope), tk = bySchool(tokens);

  const rows = schools.map((school) => {
    const silent = now - last[school];
    const status = silent < 600 ? `<span class="ok">En ligne</span>` : `<span class="bad">Muet depuis ${ago(silent)}</span>`;
    const version = server?.find((s) => s.metric.school === school)?.metric.server_version ?? "—";
    const posts = (apps ?? []).filter((s) => s.metric.school === school && Number(s.value[1]) > 0)
      .map((s) => `${escape(s.metric.version)} ×${num(Number(s.value[1]))}`).join(", ") || "—";
    const lic = licence?.find((s) => s.metric.school === school)?.metric;
    const licText = lic ? `${LICENCE_LABEL[lic.status] ?? escape(lic.status)}${lic.tier !== "none" ? ` · ${escape(lic.tier)}` : ""}${dl[school] !== undefined ? ` · ${num(dl[school])} j` : ""}` : "—";
    return `<tr><td>${escape(school)}</td><td>${status}</td><td>${escape(version)}</td><td>${posts}</td><td>${num(m[school])}</td><td>${num(a[school])}</td><td>${num(d[school])}</td><td>${licText}</td></tr>`;
  }).join("");

  const usage = schools.map((school) => `<h3>${escape(school)}</h3><div class="charts">
<figure><figcaption>Dictées par jour (14 j)</figcaption>${bars(points(dictSeries, school), "dictées")}</figure>
<figure><figcaption>Temps de transcription, 95 % des dictées (s)</figcaption>${bars(points(latency, school), "s")}</figure></div>`).join("");

  const ai = schools.map((school) => {
    const month = en[school] !== undefined ? en[school] / 12 : undefined;
    const share = month ? `${num((100 * (sp[school] ?? 0)) / month)} %` : "—";
    return `<tr><td>${escape(school)}</td><td>${num(sp[school], 2)} €</td><td>${en[school] !== undefined ? `${num(en[school])} €` : "Non fixée"}</td><td>${month ? `${num(month, 2)} €` : "—"}</td><td>${share}</td><td>${num(tk[school])}</td></tr>`;
  }).join("");

  const supportRows = support === null
    ? `<p>Les journaux ne sont pas lisibles avec ce jeton : ajoutez le droit <em>logs:read</em> à sa politique d'accès Grafana Cloud.</p>`
    : support.length === 0
      ? `<p>Aucun signalement depuis 30 jours.</p>`
      : `<div class="scroll"><table><thead><tr><th>Date</th><th>École</th><th>Version</th><th>Moteurs à surveiller</th><th>Dernière erreur</th><th>Description</th></tr></thead><tbody>${
        support.map((r) => {
          const engines = Object.entries((r.line.engines ?? {}) as Record<string, string>)
            .filter(([, v]) => v !== "ok").map(([k, v]) => `${escape(k)} : ${escape(v)}`).join(", ") || "Tout va bien";
          return `<tr><td>${new Date(r.at * 1000).toISOString().slice(0, 16).replace("T", " ")}</td><td>${escape(r.school)}</td><td>${escape(r.line.app_version)}</td><td>${engines}</td><td>${escape(r.line.last_error) || "—"}</td><td>${escape(r.line.description) || "—"}</td></tr>`;
        }).join("")}</tbody></table></div>`;

  return `<section><h2>Écoles</h2><div class="scroll"><table><thead><tr><th>École</th><th>Serveur</th><th>Version</th><th>Postes</th><th>Comptes actifs</th><th>Élèves actifs (mois)</th><th>Dictées 24 h</th><th>Licence</th></tr></thead>
<tbody>${rows || `<tr><td colspan="8">Aucune école ne remonte de mesures.</td></tr>`}</tbody></table></div></section>
<section><h2>Usage</h2>${usage}</section>
<section><h2>Coûts d'IA</h2><p>Mois en cours, comparé au douzième de l'enveloppe annuelle de la licence.</p><div class="scroll"><table><thead><tr><th>École</th><th>Dépensé ce mois</th><th>Enveloppe annuelle</th><th>Part du mois</th><th>Consommé</th><th>Jetons (30 j)</th></tr></thead>
<tbody>${ai}</tbody></table></div></section>
<section><h2>Signalements (30 j)</h2>${supportRows}</section>`;
}
