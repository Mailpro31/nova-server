/**
 * Limites : la durée maximale d'une dictée pour toute l'organisation.
 *
 * Le serveur stocke des secondes (30 à 7 200, 600 par défaut) ; la console
 * parle en minutes entières, de 1 à 120. Le PUT de la policy **remplace** le
 * document : on renvoie toujours tous les réglages, sans quoi une fonction
 * fermée ailleurs reprendrait sa valeur par défaut.
 */

export const DICTATION_LIMIT_KEY = "max_dictation_seconds";

/** Le défaut du serveur, affiché tant que l'organisation n'a rien choisi. */
export const DEFAULT_DICTATION_MINUTES = 10;
export const MIN_DICTATION_MINUTES = 1;
export const MAX_DICTATION_MINUTES = 120;

export type PolicySettings = Record<string, boolean | number>;
export type LimitProblem = "empty" | "whole" | "range";

export function dictationLimitMinutes(settings: PolicySettings): number {
  const value = settings[DICTATION_LIMIT_KEY];
  return typeof value === "number"
    ? Math.round(value / 60)
    : DEFAULT_DICTATION_MINUTES;
}

export function withDictationLimit(
  settings: PolicySettings,
  minutes: number,
): PolicySettings {
  return { ...settings, [DICTATION_LIMIT_KEY]: minutes * 60 };
}

/** Ce qui empêche d'enregistrer la valeur saisie, `null` quand elle convient. */
export function limitProblem(input: string): LimitProblem | null {
  const trimmed = input.trim();
  const value = Number(trimmed);
  if (trimmed === "" || !Number.isFinite(value)) return "empty";
  if (!Number.isInteger(value)) return "whole";
  if (value < MIN_DICTATION_MINUTES || value > MAX_DICTATION_MINUTES)
    return "range";
  return null;
}
