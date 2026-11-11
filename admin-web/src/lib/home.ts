/**
 * La page d'accueil (lot 3, 11/11) : d'abord « est-ce que tout marche ? »,
 * puis les chiffres que le serveur fournit, les alertes et les raccourcis.
 */

export type EngineSummary = "ok" | "warn" | "down";

/** L'état du moteur d'IA en un mot, pour une phrase lisible par tous. */
export function engineSummary(
  status: "unavailable" | "starting" | "healthy" | "degraded",
): EngineSummary {
  if (status === "healthy") return "ok";
  if (status === "unavailable") return "down";
  return "warn";
}

/** Les tâches courantes d'une direction ou d'un référent numérique. */
export const HOME_SHORTCUTS = [
  { path: "members", key: "members" },
  { path: "usage", key: "usage" },
  { path: "groups", key: "groups" },
  { path: "audit", key: "audit" },
] as const;
