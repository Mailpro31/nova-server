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

// ------------------------------------------------------------ données de la flotte

export type Tone = "ok" | "warn" | "bad" | "neutral";
export const LICENCE_TONE: Record<string, Tone> = {
  valid: "ok", warning: "warn", grace: "warn", expired: "bad", none: "neutral", invalid: "bad",
};
export { ago, num, LICENCE_LABEL, DAY };

/** Un serveur est « en ligne » s'il a envoyé ses mesures depuis moins de 10 minutes. */
export const ONLINE_SECONDS = 600;

export interface SchoolData {
  id: string;                      // étiquette « school » de Grafana
  lastSeen: number;                // secondes Unix du dernier envoi
  online: boolean;
  serverVersion?: string;
  apps: { version: string; count: number }[];   // versions de Nova sur les postes
  members?: number;                // comptes actifs
  activeMonth?: number;            // élèves ayant dicté ce mois
  dictations24h?: number;
  licence?: { status: string; tier: string; daysLeft?: number };
  spendMonth?: number;             // € dépensés ce mois
  envelopeAnnual?: number;         // € par an, si la licence en porte une
  tokens30d?: number;
  dictSeries: [number, number][];  // dictées par jour, 14 jours
  latencySeries: [number, number][]; // p95 de la transcription (s) par jour, 14 jours
}
export interface Fleet { schools: SchoolData[]; reports: Report[] | null }
export type FleetResult = { ok: true; fleet: Fleet } | { ok: false; reason: "token" | "grafana" };

/** Lit tout Grafana Cloud en une passe (requêtes en parallèle). */
export async function loadFleet(env: MetricsEnv, now: number): Promise<FleetResult> {
  if (!env.GRAFANA_READ_TOKEN) return { ok: false, reason: "token" };
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
      instant(env, 'max by (school) (nova_ai_envelope_eur{kind="annual"})'),
      instant(env, "sum by (school) (increase(nova_ai_tokens_total[30d]))"),
      range(env, 'sum by (school) (increase(nova_events_total{event="inference_transcribe"}[1d]))', now, 14),
      range(env, 'histogram_quantile(0.95, sum by (school, le) (rate(nova_stage_seconds_bucket{stage="transcription"}[1d])))', now, 14),
      reports(env, now),
    ]);
  if (!seen) return { ok: false, reason: "grafana" };
  const last = bySchool(seen), m = bySchool(members), a = bySchool(active), d = bySchool(dictations), dl = bySchool(days);
  const sp = bySchool(spend), en = bySchool(envelope), tk = bySchool(tokens);
  const schools = Object.keys(last).sort().map((id): SchoolData => {
    const lic = licence?.find((s) => s.metric.school === id)?.metric;
    return {
      id, lastSeen: last[id], online: now - last[id] < ONLINE_SECONDS,
      serverVersion: server?.find((s) => s.metric.school === id)?.metric.server_version,
      apps: (apps ?? []).filter((s) => s.metric.school === id && Number(s.value[1]) > 0)
        .map((s) => ({ version: s.metric.version, count: Number(s.value[1]) })),
      members: m[id], activeMonth: a[id], dictations24h: d[id],
      licence: lic ? { status: lic.status, tier: lic.tier, daysLeft: dl[id] } : undefined,
      spendMonth: sp[id], envelopeAnnual: en[id], tokens30d: tk[id],
      dictSeries: points(dictSeries, id), latencySeries: points(latency, id),
    };
  });
  return { ok: true, fleet: { schools, reports: support } };
}
