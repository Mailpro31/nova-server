/**
 * La barre latérale, rangée par domaines d'administration.
 *
 * Onze pages dans une seule liste obligeaient à savoir où chercher. Les
 * domaines suivent l'ordre dans lequel on administre une organisation : qui
 * sont les membres, ce que l'application leur ouvre, ce qu'ils apprennent, le
 * contenu qu'ils reçoivent, puis la connexion, le journal et l'infrastructure.
 *
 * Une table plutôt que du JSX : un test vérifie que chaque page routée y
 * figure une fois, et l'écran ne peut pas en perdre une en route.
 */

export type NavSectionId =
  | "overview"
  | "people"
  | "features"
  | "content"
  | "tracking"
  | "audit"
  | "advanced";

export interface NavSection {
  id: NavSectionId;
  /** Les adresses des pages du domaine, dans l'ordre d'affichage. */
  pages: readonly string[];
  /** Replié par défaut : les réglages techniques, qu'on ouvre rarement. */
  collapsed?: boolean;
}

export const NAV_SECTIONS: readonly NavSection[] = [
  { id: "overview", pages: ["overview"] },
  { id: "people", pages: ["members", "groups", "administrators", "provisioning"] },
  { id: "features", pages: ["configuration", "learn"] },
  { id: "content", pages: ["packages"] },
  // Le suivi : ce qu'une direction consulte. Rangé sous « Limites », on ne l'y
  // cherchait pas (demande de Sash, 11/11).
  { id: "tracking", pages: ["usage", "adoption", "subscription", "limits"] },
  { id: "audit", pages: ["audit"] },
  // Les réglages techniques, repliés : une direction n'y va presque jamais.
  { id: "advanced", pages: ["identity", "security", "deployment", "diagnostics"], collapsed: true },
];
