/**
 * Un nom de groupe qui dit un diagnostic (2.5) : « Dys L3 », « Tiers temps
 * MDPH »… Tous les membres d'un groupe visible voient son nom, et
 * l'appartenance à un tel groupe révèle une donnée de santé (RGPD, art. 9).
 * La console avertit ; elle ne bloque pas.
 */

// shortcut: une liste de mots en français et en anglais, à compléter quand
// une école signale un nom passé à travers.
// Les bornes se disent en Unicode : `\b` ignore les lettres accentuées.
const MEDICAL =
  /(?<![\p{L}\p{N}])(dys\p{L}*|tdah?|tda\/h|adhd|autis\p{L}*|asperger|tsa|handicap\p{L}*|disabilit\p{L}*|mdph|pap|pai|pps|tiers[- ]temps|am[ée]nagements?|m[ée]dica\p{L}*|sant[ée]|health|troubles?)(?![\p{L}\p{N}])/iu;

export function looksMedical(name: string): boolean {
  return MEDICAL.test(name);
}
