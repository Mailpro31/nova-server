/**
 * Sas de transit d'un aller-retour d'autorisation.
 *
 * ## Pourquoi il existe
 *
 * Envoyer l'utilisateur chez son fournisseur d'identite **detruit la page** :
 * `window.location.assign` decharge le document, et tout ce qui vivait en
 * memoire disparait. Au retour, la console n'avait plus ni `code_verifier`, ni
 * identifiant de tentative, ni session — l'echange n'etait meme pas tente, et
 * l'utilisateur retombait sur l'ecran de depart sans explication.
 *
 * Un etat purement en memoire est incompatible avec une redirection complete.
 * Ce module est la reponse minimale : un sas, pas un coffre.
 *
 * ## Ce qu'il contient, et pour combien de temps
 *
 * Uniquement ce qu'un aller-retour exige, et seulement pendant celui-ci :
 * le `code_verifier`, l'identifiant de tentative, l'adresse de retour,
 * l'organisation, et — pour une reauthentification — le jeton de session
 * utilisateur, sans lequel le serveur ne saurait pas qui demande.
 *
 * Il est **efface des consommation**, avant meme que le resultat soit exploite.
 *
 * ## Ce qu'il ne contient jamais
 *
 * **Le jeton d'administration.** Celui-la est obtenu au dernier retour, n'a
 * aucune redirection a traverser, et reste exclusivement en memoire. C'est la
 * propriete qui compte : un script qui lirait ce stockage n'y trouverait jamais
 * de quoi administrer.
 *
 * `sessionStorage` et non `localStorage` : le sas meurt avec l'onglet.
 */

const KEY = "nova-admin.handoff";

export interface Handoff {
  kind: "sign_in" | "step_up";
  flowId: string;
  verifier: string;
  redirect: string;
  providerType: string;
  organizationSlug: string;
  serviceEndpoint: string;
  organizationName: string;
  /** Present uniquement pour une reauthentification. */
  userToken?: string;
}

export function rememberHandoff(handoff: Handoff): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(handoff));
  } catch {
    // Stockage indisponible (navigation privee stricte, quota) : le parcours
    // echouera au retour avec un message clair plutot que silencieusement.
  }
}

/** Lit le sas **et le vide**, dans le meme geste. */
export function consumeHandoff(): Handoff | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return raw ? (JSON.parse(raw) as Handoff) : null;
  } catch {
    return null;
  }
}

export function clearHandoff(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* rien a nettoyer */
  }
}

/**
 * Organisation du parcours en cours, **sans vider le sas**.
 *
 * L'ecran de retour a besoin de savoir ou renvoyer l'utilisateur, et il le sait
 * avant que la session soit reconstruite. C'est une lecture seule : la
 * consommation reste un geste unique, ailleurs.
 */
export function peekHandoffOrganization(): string | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? ((JSON.parse(raw) as Handoff).organizationSlug || null) : null;
  } catch {
    return null;
  }
}
