/**
 * Modèle de session de la console.
 *
 * ## Où vivent les jetons
 *
 * **En mémoire, et nulle part ailleurs.** Ni `localStorage`, ni
 * `sessionStorage`, ni cookie, ni URL.
 *
 * Un jeton d'administration écrit dans `localStorage` est lisible par tout
 * script qui parvient à s'exécuter dans la page, et il y survit à la fermeture
 * de l'onglet : le vol devient permanent. En mémoire, il disparaît au
 * rafraîchissement — l'administrateur repasse alors par la réauthentification,
 * ce qui coûte quelques secondes et vaut la propriété obtenue.
 *
 * Conséquence sur le CSRF : il n'y en a pas à défendre ici. Le navigateur
 * n'envoie rien automatiquement, puisque rien n'est ambiant ; une page tierce
 * peut déclencher une requête, elle ne peut pas y joindre le jeton. C'est aussi
 * pourquoi `credentials: "omit"` est explicite dans le client.
 *
 * La cible reste un cookie `HttpOnly` + `Secure` + `SameSite` — voir
 * `docs/architecture/nova-admin-foundation.md`. Elle demandera alors une
 * défense CSRF explicite, que `SameSite` seul ne fournit pas.
 */

export type SessionPhase =
  | "discovering"
  | "signed_out"
  | "signed_in"
  | "step_up_required"
  | "administering"
  | "access_denied";

export interface AdminCapabilities {
  role: string;
  capabilities: string[];
  organizationId: string;
  expiresAt: number;
}

export interface SessionState {
  phase: SessionPhase;
  organizationSlug: string | null;
  organizationName: string | null;
  serviceEndpoint: string | null;
  userToken: string | null;
  admin: AdminCapabilities | null;
  adminToken: string | null;
}

export const initialSession: SessionState = {
  phase: "discovering",
  organizationSlug: null,
  organizationName: null,
  serviceEndpoint: null,
  userToken: null,
  admin: null,
  adminToken: null,
};

/**
 * Une capacité est-elle accordée ?
 *
 * L'interface se règle sur les **capacités**, pas sur le nom du rôle. Comparer
 * des noms de rôle dans les composants recréerait la matrice d'autorisation
 * côté client, et elle finirait par diverger de celle du serveur.
 *
 * Ce que cela cache n'est jamais une protection : le serveur reste l'autorité,
 * et refuse de toute façon. Masquer un bouton évite un échec inutile, rien de
 * plus.
 */
export function can(session: SessionState, capability: string): boolean {
  return session.admin?.capabilities.includes(capability) ?? false;
}

export const CAPABILITY = {
  organizationRead: "organization_read",
  providerManage: "provider_manage",
  discoveryManage: "discovery_manage",
  deploymentManage: "deployment_manage",
  identityManage: "identity_manage",
  securityManage: "security_manage",
  diagnosticsRead: "diagnostics_read",
  provisioningManage: "provisioning_manage",
  // Modifier les policies produit. Distincte des autres a dessein :
  // gouverner ce que l'organisation offre a ses membres n'est ni gerer
  // des personnes, ni administrer la machine.
  policyManage: "policy_manage",
  // Preparer et publier le contenu distribue. Une seule capacite pour les
  // trois types : une par type ferait grossir la matrice sans repondre a
  // une question differente.
  contentManage: "content_manage",
} as const;

/** Secondes restantes avant expiration, jamais négatives. */
export function secondsRemaining(admin: AdminCapabilities, now = Date.now()): number {
  return Math.max(0, Math.round((admin.expiresAt - now) / 1000));
}

/**
 * Transition après un échec d'appel.
 *
 * Le point important est l'absence de boucle : une session expirée renvoie à la
 * réauthentification, pas à un cycle qui retenterait indéfiniment le même appel.
 */
export function sessionAfterFailure(
  session: SessionState,
  kind: string,
): SessionState {
  switch (kind) {
    case "admin_session_expired":
    case "step_up_required":
      return { ...session, phase: "step_up_required", admin: null, adminToken: null };
    case "unauthenticated":
      return {
        ...session,
        phase: "signed_out",
        userToken: null,
        admin: null,
        adminToken: null,
      };
    default:
      return session;
  }
}
