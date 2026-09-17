/**
 * Styles désactivés par l'organisation.
 *
 * La policy porte `disabled_style_ids` : les identifiants des Styles que Nova
 * n'applique plus. Les Styles intégrés gardent l'identifiant du poste ; un
 * Style d'organisation prend celui que le serveur lui donne, `org_style_<id>`.
 */

export const DISABLED_STYLES_KEY = "disabled_style_ids";

export type PolicySettings = Record<string, boolean | number | string[]>;

export interface StyleEntry {
  id: string;
  name: string;
  origin: "builtin" | "organization";
}

/**
 * Les Styles intégrés qu'un membre d'organisation voit, dans l'ordre de la
 * page Styles de Nova. « Réunion » n'y figure pas : l'édition Organization ne
 * l'affiche pas.
 */
export const BUILTIN_STYLES: readonly StyleEntry[] = [
  {
    id: "default_improve_transcriptions",
    name: "Improved transcription",
    origin: "builtin",
  },
  { id: "nova_style_email", name: "Email", origin: "builtin" },
  { id: "nova_style_messages", name: "Messages", origin: "builtin" },
  { id: "nova_style_notes", name: "Notes", origin: "builtin" },
  { id: "nova_style_todo", name: "To-do list", origin: "builtin" },
  { id: "nova_style_prompt", name: "AI prompt", origin: "builtin" },
  { id: "nova_style_voice_to_text", name: "Voice to text", origin: "builtin" },
];

export function organizationStyleId(packageId: string): string {
  return `org_style_${packageId}`;
}

export function disabledStyleIds(settings: PolicySettings): string[] {
  const value = settings[DISABLED_STYLES_KEY];
  return Array.isArray(value) ? value : [];
}

/** Tout le document, avec ce Style ajouté à la liste ou retiré. */
export function withStyleDisabled(
  settings: PolicySettings,
  styleId: string,
  disabled: boolean,
): PolicySettings {
  const current = new Set(disabledStyleIds(settings));
  if (disabled) current.add(styleId);
  else current.delete(styleId);
  return { ...settings, [DISABLED_STYLES_KEY]: [...current].sort() };
}

/** Les Styles intégrés, puis les Styles publiés par l'organisation. */
export function stylesCatalog(
  packages: readonly {
    package_id: string;
    package_type: string;
    name: string;
    status: string;
  }[],
): StyleEntry[] {
  return [
    ...BUILTIN_STYLES,
    ...packages
      .filter(
        (item) => item.package_type === "style" && item.status === "published",
      )
      .map((item) => ({
        id: organizationStyleId(item.package_id),
        name: item.name,
        origin: "organization" as const,
      })),
  ];
}
