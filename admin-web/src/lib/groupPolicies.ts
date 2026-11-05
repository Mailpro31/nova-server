/**
 * Réglages par groupe : ce que l'écran manipule.
 *
 * Un groupe ne porte que des **surcharges**. Une clé absente suit
 * l'organisation ; `true` ou `false` est un choix fait pour ce groupe, même
 * quand il coïncide avec la valeur de l'organisation — il continuera de
 * s'appliquer si l'organisation change d'avis.
 */

export type OverrideChoice = "inherit" | "on" | "off";
export type GroupOverrides = Record<string, boolean | number>;

export const DICTATION_KEY = "max_dictation_seconds";

export function choiceOf(overrides: GroupOverrides, key: string): OverrideChoice {
  const value = overrides[key];
  if (value === true) return "on";
  if (value === false) return "off";
  return "inherit";
}

export function applyChoice(
  overrides: GroupOverrides,
  key: string,
  choice: OverrideChoice,
): GroupOverrides {
  const next = { ...overrides };
  if (choice === "inherit") delete next[key];
  else next[key] = choice === "on";
  return next;
}

/** La limite de dictée du groupe en minutes, `null` quand il suit l'organisation. */
export function dictationMinutes(overrides: GroupOverrides): number | null {
  const value = overrides[DICTATION_KEY];
  return typeof value === "number" ? Math.round(value / 60) : null;
}

export function withDictationMinutes(
  overrides: GroupOverrides,
  minutes: number | null,
): GroupOverrides {
  const next = { ...overrides };
  if (minutes === null || !Number.isFinite(minutes)) delete next[DICTATION_KEY];
  else next[DICTATION_KEY] = Math.round(minutes) * 60;
  return next;
}

export const EXAM_KEY = "exam_until";

/** La fin d'un examen en cours, en secondes ; `null` sans examen en cours. */
export function examUntil(overrides: GroupOverrides, now = Date.now()): number | null {
  const value = overrides[EXAM_KEY];
  return typeof value === "number" && value * 1000 > now ? value : null;
}

/** Un examen de `hours` heures à partir de maintenant ; `null` l'arrête. */
export function withExamHours(
  overrides: GroupOverrides,
  hours: number | null,
  now = Date.now(),
): GroupOverrides {
  const next = { ...overrides };
  if (hours === null) delete next[EXAM_KEY];
  else next[EXAM_KEY] = Math.floor(now / 1000) + Math.round(hours * 3600);
  return next;
}

export function countOverrides(overrides: GroupOverrides): number {
  return Object.keys(overrides).length;
}

export function sameOverrides(left: GroupOverrides, right: GroupOverrides): boolean {
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  return [...keys].every((key) => left[key] === right[key]);
}
