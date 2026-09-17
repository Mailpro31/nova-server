/**
 * Fournisseurs d'identité : ce que la console envoie.
 *
 * Le serveur remplace la configuration entière à chaque écriture. La console
 * renvoie donc tout ce qu'elle a lu, plus le seul changement voulu — et jamais
 * un secret qu'elle n'a pas saisi : le serveur ne le renvoie pas, et un champ
 * vide garde le secret existant.
 */

import type { ProviderConfigView } from "./controlPlane";

export const PROVIDER_TYPES = [
  "microsoft_entra",
  "google_workspace",
  "oidc",
] as const;
export const OIDC_AUTH_METHODS = [
  "client_secret_post",
  "client_secret_basic",
  "none",
] as const;

export interface ProviderDraft {
  provider_type: string;
  display_name?: string | null;
  enabled?: boolean;
  entra_tenant_id?: string | null;
  google_hosted_domain?: string | null;
  oidc_issuer?: string | null;
  oidc_client_id?: string | null;
  oidc_client_secret?: string | null;
  oidc_client_auth_method?: string | null;
  oidc_required_claim?: string | null;
  oidc_required_claim_value?: string | null;
}

function filled(value: string | null | undefined): boolean {
  return typeof value === "string" && value.trim() !== "";
}

/** La configuration lue, avec les changements demandés. Sans secret, sauf s'il vient d'être saisi. */
export function providerPayload(
  config: ProviderConfigView,
  changes: Partial<ProviderDraft>,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    provider_type: config.provider_type,
    display_name: config.display_name,
    enabled: config.enabled,
    entra_tenant_id: config.entra_tenant_id ?? null,
    google_hosted_domain: config.google_hosted_domain ?? null,
    oidc_issuer: config.oidc_issuer ?? null,
    oidc_client_id: config.oidc_client_id ?? null,
    oidc_client_auth_method: config.oidc_client_auth_method ?? null,
    oidc_allow_private_issuer: config.oidc_allow_private_issuer ?? false,
    oidc_required_claim: config.oidc_required_claim ?? null,
    oidc_required_claim_value: config.oidc_required_claim_value ?? null,
    ...changes,
  };
  if (!filled(changes.oidc_client_secret)) delete body.oidc_client_secret;
  return body;
}

/** Ce qui manque encore pour ajouter ce fournisseur, champ par champ. Vide si rien. */
export function newProviderProblems(draft: ProviderDraft): string[] {
  const problems: string[] = [];
  if (draft.provider_type === "microsoft_entra") {
    if (!filled(draft.entra_tenant_id)) problems.push("entra_tenant_id");
  } else if (draft.provider_type === "google_workspace") {
    if (!filled(draft.google_hosted_domain))
      problems.push("google_hosted_domain");
  } else if (draft.provider_type === "oidc") {
    if (
      !filled(draft.oidc_issuer) ||
      !draft.oidc_issuer!.trim().startsWith("https://")
    ) {
      problems.push("oidc_issuer");
    }
    if (!filled(draft.oidc_client_id)) problems.push("oidc_client_id");
    if (
      draft.oidc_client_auth_method !== "none" &&
      !filled(draft.oidc_client_secret)
    ) {
      problems.push("oidc_client_secret");
    }
  } else {
    problems.push("provider_type");
  }
  return problems;
}
