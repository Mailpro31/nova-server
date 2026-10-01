/**
 * Le tableau de bord d'usage : ce qui se calcule avant de se dessiner.
 *
 * Tout ce qui décide d'une forme vit ici, en fonctions pures, pour être testé
 * sans rendre un composant : les échelles, le tracé, les libellés d'heure et
 * l'export CSV. La page ne garde que le JSX.
 *
 * ## Pourquoi du SVG et pas une bibliothèque de graphiques
 *
 * Cinq séries à tracer. Une bibliothèque coûterait plus que le code qu'elle
 * remplace — c'est déjà l'argument retenu côté serveur pour l'exposition
 * Prometheus — et imposerait son esthétique là où la charte Nova doit tenir.
 */

export interface UsageHour {
  hour: string;
  transcriptions: number;
  reformulations: number;
  audio_seconds: number;
  people: number;
  latency_ms_average: number | null;
  latency_ms_max: number | null;
}

export interface HostSample {
  sampled_at: string;
  cpu_percent: number | null;
  ram_used_gb: number | null;
  ram_total_gb: number | null;
  gpu_percent: number | null;
  vram_used_gb: number | null;
  vram_total_gb: number | null;
  gpu_temperature_c: number | null;
  gpu_power_w: number | null;
}

export interface UsageMember {
  email: string;
  transcriptions: number;
  reformulations: number;
  audio_seconds: number;
  latency_ms_average: number | null;
}

export interface UsageSummary {
  dictations: number;
  people_peak: number;
  busiest_hour: string | null;
  latency_ms_average: number | null;
  latency_ms_max: number | null;
  over_five_seconds: number;
}

export interface UsageMetrics {
  since: string;
  until: string;
  summary: UsageSummary;
  timeline: readonly UsageHour[];
  host: readonly HostSample[];
  members?: readonly UsageMember[];
}

/** L'objectif de bout en bout, en millisecondes. Au-delà, on le signale. */
export const LATENCY_OBJECTIVE_MS = 5000;

/** Les fenêtres proposées, en heures. 31 jours est la borne du serveur. */
export const WINDOWS = [24, 24 * 7, 24 * 31] as const;

export interface Plot {
  /** La largeur et la hauteur du repère, en unités SVG. */
  width: number;
  height: number;
}

export const PLOT: Plot = { width: 720, height: 160 };

/**
 * Le maximum de l'axe vertical, arrondi vers le haut pour laisser respirer.
 *
 * Jamais zéro : une échelle nulle ferait diviser par zéro et écraserait la
 * courbe sur l'axe. Une série vide rend donc 1, et la courbe est plate en bas.
 */
export function axisMaximum(values: readonly (number | null)[]): number {
  const known = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const peak = known.length ? Math.max(...known) : 0;
  if (peak <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  return Math.ceil(peak / magnitude) * magnitude;
}

/**
 * Le tracé d'une série, en coordonnées SVG.
 *
 * Les trous restent des trous : une heure sans mesure coupe la ligne au lieu
 * d'être lue comme un zéro. Une latence absente veut dire « personne n'a
 * dicté », pas « la réponse a été instantanée ».
 */
export function linePath(
  values: readonly (number | null)[],
  maximum: number,
  plot: Plot = PLOT,
): string {
  if (values.length === 0) return "";
  const step = values.length === 1 ? 0 : plot.width / (values.length - 1);
  let path = "";
  let drawing = false;
  values.forEach((value, index) => {
    if (value === null || !Number.isFinite(value)) {
      drawing = false;
      return;
    }
    const x = Math.round(index * step * 100) / 100;
    const y = Math.round((plot.height - (value / maximum) * plot.height) * 100) / 100;
    path += `${drawing ? "L" : "M"}${x} ${y}`;
    drawing = true;
  });
  return path;
}

/** Les barres d'un histogramme, une par heure. */
export function bars(
  values: readonly number[],
  maximum: number,
  plot: Plot = PLOT,
): { x: number; y: number; width: number; height: number }[] {
  if (values.length === 0) return [];
  const slot = plot.width / values.length;
  const width = Math.max(1, Math.round(slot * 0.7 * 100) / 100);
  return values.map((value, index) => {
    const height = Math.max(0, Math.round((value / maximum) * plot.height * 100) / 100);
    return {
      x: Math.round((index * slot + (slot - width) / 2) * 100) / 100,
      y: Math.round((plot.height - height) * 100) / 100,
      width,
      height,
    };
  });
}

/**
 * L'heure telle qu'on la lit : « 14 h » sur un jour, « 12/05 14 h » au-delà.
 *
 * Une clé `2026-05-12T14` est exacte et illisible ; sur une semaine, l'heure
 * seule ne suffit plus à situer le point.
 */
export function hourLabel(hour: string, withDay: boolean): string {
  const [date, time] = hour.split("T");
  const heure = `${Number(time)} h`;
  if (!withDay) return heure;
  const [, month, day] = date.split("-");
  return `${day}/${month} ${heure}`;
}

/** Un instant de relevé machine, ramené à l'heure et à la minute. */
export function sampleLabel(sampledAt: string): string {
  const time = sampledAt.split("T")[1] ?? "";
  return time.slice(0, 5);
}

/** Les libellés à porter sous l'axe : au plus six, répartis régulièrement. */
export function axisLabels(hours: readonly string[], withDay: boolean): string[] {
  if (hours.length === 0) return [];
  const wanted = Math.min(6, hours.length);
  const step = (hours.length - 1) / Math.max(1, wanted - 1);
  const picked: string[] = [];
  for (let i = 0; i < wanted; i += 1) {
    picked.push(hourLabel(hours[Math.round(i * step)], withDay));
  }
  return picked;
}

/** Une durée en millisecondes, lisible : « 1,2 s » plutôt que « 1200 ms ». */
export function seconds(milliseconds: number | null): string {
  if (milliseconds === null) return "—";
  return `${(milliseconds / 1000).toFixed(1).replace(".", ",")} s`;
}

/** La proportion d'une mémoire occupée, `null` quand la carte est absente. */
export function share(used: number | null, total: number | null): number | null {
  if (used === null || total === null || total <= 0) return null;
  return Math.round((used / total) * 100);
}

/** Vrai si aucun relevé de la fenêtre n'a vu de carte graphique. */
export function withoutGraphicsCard(host: readonly HostSample[]): boolean {
  return host.every((sample) => sample.gpu_percent === null);
}

function csvCell(value: string | number | null): string {
  if (value === null) return "";
  const text = String(value);
  return /[",;\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/**
 * La fenêtre affichée, en CSV, pour un rapport ou un tableur.
 *
 * Point-virgule comme séparateur : c'est ce qu'Excel attend d'un fichier en
 * français, et une virgule y collerait toutes les colonnes dans la première.
 * Les décimales restent en point, sans quoi le nombre deviendrait du texte.
 */
export function timelineCsv(timeline: readonly UsageHour[]): string {
  const header = [
    "heure",
    "transcriptions",
    "reformulations",
    "secondes_audio",
    "personnes",
    "latence_moyenne_ms",
    "latence_max_ms",
  ];
  const rows = timeline.map((hour) =>
    [
      hour.hour,
      hour.transcriptions,
      hour.reformulations,
      hour.audio_seconds,
      hour.people,
      hour.latency_ms_average,
      hour.latency_ms_max,
    ]
      .map(csvCell)
      .join(";"),
  );
  return [header.join(";"), ...rows].join("\n");
}

/** Le détail nominatif en CSV. Même règle de séparateur. */
export function membersCsv(members: readonly UsageMember[]): string {
  const header = [
    "adresse",
    "transcriptions",
    "reformulations",
    "secondes_audio",
    "latence_moyenne_ms",
  ];
  const rows = members.map((member) =>
    [
      member.email,
      member.transcriptions,
      member.reformulations,
      member.audio_seconds,
      member.latency_ms_average,
    ]
      .map(csvCell)
      .join(";"),
  );
  return [header.join(";"), ...rows].join("\n");
}
