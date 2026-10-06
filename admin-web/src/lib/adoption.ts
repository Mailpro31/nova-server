/**
 * L'adoption de Nova, en comptes : ce que la direction et la CTI lisent.
 *
 * Le serveur (`adoption_report`) rend douze semaines et les mois qu'il garde,
 * sans aucun nom. Une période où une ou deux personnes seulement ont dicté
 * arrive masquée : ses comptes valent `null`, et la console l'écrit en clair
 * plutôt que de laisser croire à un zéro.
 */

export interface AdoptionCounts {
  people: number | null;
  regular_people?: number | null;
  dictations: number | null;
  reformulations: number | null;
  audio_minutes: number | null;
  masked: boolean;
  /** Faux pour une période d'avant le premier relevé : ses comptes sont inconnus, pas nuls. */
  measured: boolean;
  /** Vrai pour la période où le relevé a commencé : elle n'est pas complète. */
  partial: boolean;
}

export interface AdoptionWeek extends AdoptionCounts {
  week_start: string;
}

export interface AdoptionMonth extends AdoptionCounts {
  month: string;
}

export interface AdoptionReport {
  min_people: number;
  regular_days: number;
  /** Le premier jour mesuré (« 2026-09-29 »), ou `null` si rien ne l'est encore. */
  measured_from: string | null;
  weeks: AdoptionWeek[];
  months: AdoptionMonth[];
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** « 2026-10-05 » → « Week of Oct 5 ». */
export function weekLabel(start: string): string {
  const [, month, day] = start.split("-").map(Number);
  return `Week of ${MONTHS[month - 1]} ${day}`;
}

/** « 2026-10 » → « Oct 2026 ». */
export function monthLabel(month: string): string {
  const [year, index] = month.split("-").map(Number);
  return `${MONTHS[index - 1]} ${year}`;
}

/** La dernière semaine qui a quelque chose à dire : non vide et non masquée. */
export function latestWeek(report: AdoptionReport): AdoptionWeek | null {
  for (let index = report.weeks.length - 1; index >= 0; index -= 1) {
    const week = report.weeks[index];
    if (week.measured && !week.masked && (week.people ?? 0) > 0) return week;
  }
  return null;
}

function cell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",;\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Ce qu'une période a de particulier, dit en clair dans le CSV. */
function note(counts: AdoptionCounts, minPeople: number): string {
  if (!counts.measured) return "non mesuré";
  const parts = [];
  if (counts.partial) parts.push("partiel");
  if (counts.masked) parts.push(`moins de ${minPeople} personnes`);
  return parts.join(", ");
}

/**
 * Le rapport en CSV, semaines puis mois. Même règle que le suivi d'usage :
 * point-virgule, pour qu'Excel l'ouvre en français. Une période masquée le
 * dit dans sa colonne, au lieu de laisser des cases vides qu'on lirait comme
 * des zéros.
 */
export function adoptionCsv(report: AdoptionReport): string {
  const header = [
    "periode",
    "debut",
    "personnes",
    "personnes_regulieres",
    "dictees",
    "reformulations",
    "minutes_dictees",
    "remarque",
  ];
  const row = (kind: string, start: string, counts: AdoptionCounts) =>
    [
      kind,
      start,
      counts.people,
      counts.regular_people,
      counts.dictations,
      counts.reformulations,
      counts.audio_minutes,
      note(counts, report.min_people),
    ]
      .map(cell)
      .join(";");
  return [
    header.join(";"),
    ...report.weeks.map((week) => row("semaine", week.week_start, week)),
    ...report.months.map((month) => row("mois", month.month, month)),
  ].join("\n");
}
