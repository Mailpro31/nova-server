/**
 * Appels du Control Plane utilisés par la console.
 *
 * Types explicites plutôt que `any` : quand le serveur changera, c'est le
 * compilateur qui le dira, pas un écran vide en production.
 */

import { ApiClient } from "./api";

export interface OrganizationView {
  organization: {
    id: string;
    slug: string | null;
    display_name: string;
    org_type: string;
    status: string;
  };
  deployment: { server_mode: string; deployment_mode: string };
  discovery: { enabled: boolean; slug: string | null; service_endpoint: string };
  /** Compteur réel : c'est lui qui refuse une connexion de plus. */
  seats: { used: number; limit: number };
  providers: { provider_type: string; count: number; enabled_count: number }[];
}

export interface ProviderConfigView {
  id: string;
  provider_type: string;
  display_name: string | null;
  enabled: boolean;
  entra_tenant_id?: string | null;
  google_hosted_domain?: string | null;
  oidc_issuer?: string | null;
  oidc_client_id?: string | null;
  oidc_client_auth_method?: string | null;
  oidc_allow_private_issuer?: boolean;
  oidc_required_claim?: string | null;
  oidc_required_claim_value?: string | null;
  /** Le secret lui-même n'est jamais renvoyé — seulement son existence. */
  has_secret: boolean;
  validation_error: string | null;
}

export interface DiscoveryConfigView {
  discovery_slug: string | null;
  discovery_enabled: boolean;
  deployment_mode: string | null;
  service_endpoint: string;
  status: string | null;
  service_endpoint_error: string | null;
}

/**
 * Policy produit de l'organisation.
 *
 * A ne pas confondre avec les capacites d'administration de `session.ts` :
 * celles-ci disent ce qu'un administrateur peut modifier ; celle-la dit ce que
 * l'organisation autorise a ses membres dans Nova.
 */
export interface PolicyView {
  schema_version: number;
  /** Monotone. Sert a refuser une ecriture partie d'une version perimee. */
  revision: number;
  settings: Record<string, boolean>;
  updated_at: number | null;
  updated_by_user_id: string | null;
  /** `false` tant que personne n'a rien change : ce sont les defauts. */
  configured: boolean;
}

export interface AdminSessionView {
  token: string;
  role: string;
  organization_id: string;
  capabilities: string[];
  expires_in: number;
}

export interface AuthProvidersView {
  providers: string[];
  display_names: Record<string, string>;
  provider_configs: { id: string; type: string; display_name: string | null }[];
}

export interface AdministratorView {
  user_id: string;
  email: string;
  security_role: string;
  capabilities: string[];
  status: string;
  identity_provider: string | null;
  last_seen: number | null;
}

export interface AuditEntry {
  id: string;
  created_at: number;
  actor_user_id: string | null;
  /** Absent quand l'acte vient du jeton hérité, qui n'identifie personne. */
  actor: string | null;
  actor_role: string;
  action: string;
  action_label: string;
  target_type: string | null;
  target_id: string | null;
  target: string | null;
  request_id: string | null;
  metadata: Record<string, unknown> | null;
}

export interface MemberGroup {
  id: string;
  label: string;
  source: string;
}

export interface MemberView {
  user_id: string;
  email: string;
  member_type: string | null;
  member_role: string;
  security_role: string;
  status: string;
  identity_provider: string | null;
  last_seen: number | null;
  created_at: number | null;
  note: string | null;
  groups?: MemberGroup[];
}

export interface MemberDetail {
  member: MemberView;
  /** Libellé et dates seulement — jamais un jeton, ni son empreinte. */
  devices: {
    machine: string | null;
    active: boolean;
    created_at: number | null;
    expires_at: number | null;
  }[];
  identities: { provider: string; verification: string; last_seen: number | null }[];
  open_admin_sessions: number;
}

export class ControlPlane {
  constructor(private readonly client: ApiClient) {}

  health() {
    return this.client.request<{ status: string; version: string }>("/api/health", {
      token: null,
    });
  }

  authProviders() {
    return this.client.request<AuthProvidersView>("/api/auth/providers", {
      token: null,
    });
  }

  /** Ouvre la réauthentification. Le jeton utilisateur est présenté explicitement. */
  startStepUp(
    userToken: string,
    body: { code_challenge: string; nonce: string; redirect_uri: string },
  ) {
    return this.client.request<{
      flow_id: string;
      authorization_url: string;
      expires_in: number;
    }>("/api/admin/step-up/start", {
      method: "POST",
      body: { ...body, machine: "nova-admin" },
      token: userToken,
    });
  }

  completeStepUp(
    userToken: string,
    body: {
      flow_id: string;
      code: string;
      code_verifier: string;
      redirect_uri: string;
    },
  ) {
    return this.client.request<AdminSessionView>("/api/admin/step-up/complete", {
      method: "POST",
      body,
      token: userToken,
    });
  }

  organization() {
    return this.client.request<OrganizationView>("/api/control/organization");
  }

  providerConfigs() {
    return this.client.request<{ configs: ProviderConfigView[] }>(
      "/api/admin/provider-configs",
    );
  }

  discoveryConfig() {
    return this.client.request<DiscoveryConfigView>("/api/admin/discovery");
  }

  updateProviderConfig(id: string, body: Record<string, unknown>) {
    return this.client.request<ProviderConfigView>(
      `/api/admin/provider-configs/${id}`,
      { method: "POST", body },
    );
  }

  disableProviderConfig(id: string) {
    return this.client.request<ProviderConfigView>(
      `/api/admin/provider-configs/${id}/disable`,
      { method: "POST", body: {} },
    );
  }

  policy() {
    return this.client.request<PolicyView>("/api/control/organization/policy");
  }

  /**
   * Remplace la policy. `expectedRevision` est obligatoire : sans elle, deux
   * consoles ouvertes cote a cote s'ecraseraient en silence.
   */
  updatePolicy(expectedRevision: number, settings: Record<string, boolean>) {
    return this.client.request<PolicyView>("/api/control/organization/policy", {
      method: "PUT",
      body: { expected_revision: expectedRevision, settings },
    });
  }

  updateDiscovery(body: Record<string, unknown>) {
    return this.client.request<DiscoveryConfigView>("/api/admin/discovery", {
      method: "POST",
      body,
    });
  }

  administrators(organizationId: string) {
    return this.client.request<{
      administrators: AdministratorView[];
      sole_identity_manager: string | null;
    }>(`/api/control/organizations/${organizationId}/administrators`);
  }

  /**
   * Change le rôle d'un compte.
   *
   * Par `user_id`, jamais par adresse : une adresse est un attribut mutable, et
   * muter un privilège en la désignant reviendrait à viser une cible qui bouge.
   */
  setAdministratorRole(organizationId: string, userId: string, role: string) {
    return this.client.request<AdministratorView>(
      `/api/control/organizations/${organizationId}/administrators/${userId}/role`,
      { method: "POST", body: { security_role: role } },
    );
  }

  members(
    organizationId: string,
    options: { query?: string; status?: string; limit?: number } = {},
  ) {
    const params = new URLSearchParams();
    if (options.query) params.set("query", options.query);
    if (options.status) params.set("status", options.status);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.toString();
    return this.client.request<{
      members: MemberView[];
      has_more: boolean;
      limit: number;
      total: number;
    }>(
      `/api/control/organizations/${organizationId}/members${query ? `?${query}` : ""}`,
    );
  }

  member(organizationId: string, userId: string) {
    return this.client.request<MemberDetail>(
      `/api/control/organizations/${organizationId}/members/${userId}`,
    );
  }

  setMemberStatus(organizationId: string, userId: string, status: string) {
    return this.client.request<{ member: MemberView; changed: boolean }>(
      `/api/control/organizations/${organizationId}/members/${userId}/status`,
      { method: "POST", body: { status } },
    );
  }

  updateMemberProfile(
    organizationId: string,
    userId: string,
    body: { member_role?: string; cohort?: string; note?: string },
  ) {
    return this.client.request<{ member: MemberView; changed: boolean }>(
      `/api/control/organizations/${organizationId}/members/${userId}/profile`,
      { method: "POST", body },
    );
  }

  revokeMemberSessions(organizationId: string, userId: string) {
    return this.client.request<{ revoked_devices: number }>(
      `/api/control/organizations/${organizationId}/members/${userId}/sessions/revoke`,
      { method: "POST", body: {} },
    );
  }

  deleteMember(organizationId: string, userId: string) {
    return this.client.request<{ deleted: string }>(
      `/api/control/organizations/${organizationId}/members/${userId}`,
      { method: "DELETE" },
    );
  }

  audit(organizationId: string, options: { limit?: number; before?: number } = {}) {
    const params = new URLSearchParams();
    if (options.limit) params.set("limit", String(options.limit));
    if (options.before) params.set("before", String(options.before));
    const query = params.toString();
    return this.client.request<{
      entries: AuditEntry[];
      next_before: number | null;
      limit: number;
    }>(
      `/api/control/organizations/${organizationId}/audit${query ? `?${query}` : ""}`,
    );
  }

  signOutAdmin() {
    return this.client.request<{ revoked: boolean }>("/api/admin/session/logout", {
      method: "POST",
      body: {},
    });
  }
}
