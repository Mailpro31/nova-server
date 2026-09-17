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
  | "learn"
  | "content"
  | "limits"
  | "signIn"
  | "audit"
  | "infrastructure";

export interface NavSection {
  id: NavSectionId;
  /** Les adresses des pages du domaine, dans l'ordre d'affichage. */
  pages: readonly string[];
}

export const NAV_SECTIONS: readonly NavSection[] = [
  { id: "overview", pages: ["overview"] },
  { id: "people", pages: ["members", "groups", "administrators", "provisioning"] },
  { id: "features", pages: ["configuration"] },
  { id: "learn", pages: ["learn"] },
  { id: "content", pages: ["packages"] },
  { id: "limits", pages: ["limits"] },
  { id: "signIn", pages: ["identity", "security"] },
  { id: "audit", pages: ["audit"] },
  { id: "infrastructure", pages: ["deployment", "diagnostics"] },
];
