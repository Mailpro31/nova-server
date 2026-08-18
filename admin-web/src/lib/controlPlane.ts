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

  updateDiscovery(body: Record<string, unknown>) {
    return this.client.request<DiscoveryConfigView>("/api/admin/discovery", {
      method: "POST",
      body,
    });
  }

  signOutAdmin() {
    return this.client.request<{ revoked: boolean }>("/api/admin/session/logout", {
      method: "POST",
      body: {},
    });
  }
}
