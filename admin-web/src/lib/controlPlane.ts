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

/**
 * L'etat du runtime IA prive de l'organisation.
 *
 * Deux blocs, jamais melanges : `desired` est ce que l'administrateur a
 * demande, `reported` est ce que le runtime confirme reellement. Les afficher
 * ensemble laisserait croire qu'une configuration enregistree est une
 * configuration appliquee.
 *
 * Rien ici ne porte de contenu utilisateur : ni requete, ni transcription, ni
 * sortie de modele. Le plan de controle n'en voit jamais.
 */
export interface AIRuntimeEngineView {
  status: "ready" | "loading" | "unavailable";
  adapter: string;
  latency_ms: number | null;
  runtime_version: string | null;
  model: string | null;
  last_error_code: string | null;
}

export interface AIRuntimeView {
  schema_version: number;
  processing_location: string;
  deployment_mode: string;
  desired: {
    revision: number;
    profile: string;
    reasoning_allowed: boolean;
    max_concurrent_text: number;
    max_concurrent_speech: number;
    max_queue: number;
    cloud_fallback: string;
  };
  reported: {
    applied_revision: number;
    apply_status: string;
    runtime_status: "unavailable" | "starting" | "healthy" | "degraded";
    text_engine: AIRuntimeEngineView;
    speech_engine: AIRuntimeEngineView;
    capacity: {
      text: { max_concurrent: number; active: number; queue_length: number; max_queue: number };
      speech: { max_concurrent: number; active: number; queue_length: number; max_queue: number };
    };
    last_health_check: number;
  };
  hardware: {
    cpu: string;
    architecture: string;
    ram_gb: number | null;
    gpu_vendor: string | null;
    gpu_model: string | null;
    vram_gb: number | null;
    driver_version: string | null;
  };
  recommended_profile: string;
  /**
   * Les profils que *ce* deploiement offre reellement.
   *
   * `custom` n'y figure que si le deploiement a declare ses parametres. La
   * console suit cette liste plutot que l'enumeration complete : proposer un
   * choix refuse a l'enregistrement n'est pas proposer un choix.
   */
  available_profiles: string[];
  /** Les parametres de `custom`, en lecture seule — aucune route ne les ecrit. */
  custom_profile:
    | { available: false }
    | {
        available: true;
        max_output_tokens: number;
        temperature: number;
        reasoning: string;
      };
  /** Le plan de controle ne reconfigure pas encore un serveur prive distant. */
  remote_apply: { supported: boolean; requires_private_connector: boolean };
}

/**
 * Un Private Connector, vu du plan de controle.
 *
 * Aucun credential n'apparait ici, meme hache. Ce que la console montre sert a
 * savoir si un deploiement repond et ou en est sa configuration — pas a
 * reconstituer une identite.
 */
export interface ConnectorView {
  connector_id: string;
  deployment_id: string;
  display_name: string;
  status: "online" | "degraded" | "offline" | "revoked";
  protocol_version: number;
  protocol_supported: boolean;
  connector_version: string | null;
  server_version: string | null;
  capabilities: string[];
  runtime_state: {
    runtime_status?: string;
    text_engine?: string;
    speech_engine?: string;
    queue_length?: number;
    gpu_model?: string | null;
    vram_gb?: number | null;
    last_error_code?: string | null;
  } | null;
  delivered_revision: number;
  applied_revision: number;
  apply_status: string;
  last_error_code: string | null;
  created_at: number;
  last_seen_at: number | null;
  revoked_at: number | null;
  /**
   * L'age et l'etat de bascule du credential. Ni empreinte, ni fragment de
   * secret : rien de tout cela n'aiderait a administrer, et tout cela
   * aiderait a exploiter une fuite.
   */
  /** Ou tourne ce Connector, et s'il peut conduire sa propre bascule. */
  platform: {
    os: string | null;
    arch: string | null;
    distribution: string | null;
    self_update_supported: boolean;
    /** Qui applique la bascule : le Connector lui-meme, ou le deploiement. */
    update_mechanism: "self" | "external" | "none";
  };
  update: {
    state: string;
    policy: string;
    target_version: string | null;
    last_known_good_version: string | null;
    last_update_at: number | null;
    last_error_code: string | null;
    support_status: "up_to_date" | "update_available" | "unsupported" | "unknown";
    recommended_version: string | null;
    minimum_supported_version: string;
  };
  credential: {
    generation: number;
    created_at: number;
    age_days: number;
    rotation_state: "stable" | "requested" | "pending" | "failed";
    rotation_requested_at: number | null;
    last_rotated_at: number | null;
    rotation_supported: boolean;
  };
}

export interface ConnectorListView {
  connectors: ConnectorView[];
  pending_enrollments: number;
  protocol_version: number;
  heartbeat_seconds: number;
  offline_after_seconds: number;
}

/**
 * Un code d'enrolement fraichement cree.
 *
 * `enrollment_token` n'existe en clair qu'ici, et une seule fois : le serveur
 * n'en garde que l'empreinte et ne pourra jamais le reafficher. Il ne doit donc
 * pas etre range dans le stockage du navigateur.
 */
export interface ConnectorEnrollmentView {
  enrollment_id: string;
  enrollment_token: string;
  expires_at: number;
  expires_in: number;
  deployment_id: string;
  display_name: string;
}

export interface PackageVersionSummary {
  version_id: string;
  version_number: number;
  status: string;
  validation_status: string;
  content_hash: string;
  created_at: number;
  published_at: number | null;
  active: boolean;
}

/** Metadonnees seulement : le contenu vit sur sa propre route. */
export interface PackageView {
  package_id: string;
  package_type: string;
  name: string;
  description: string;
  status: string;
  active_version_id: string | null;
  active_version_number: number | null;
  revision: number;
  updated_at: number;
  versions: PackageVersionSummary[];
}

export interface PackageVersionDetail extends PackageVersionSummary {
  package_id: string;
  schema_version: number;
  validation_error: string | null;
  revision: number;
  content: Record<string, unknown>;
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

  aiRuntime() {
    return this.client.request<AIRuntimeView>(
      "/api/control/organization/ai-runtime",
    );
  }

  /**
   * Enregistre la configuration *souhaitee* du runtime.
   *
   * Comme pour les policies, `expectedRevision` est obligatoire : deux
   * consoles ouvertes cote a cote ne doivent pas s'ecraser. La reponse porte
   * l'etat reellement rapporte par le runtime, jamais l'echo de l'envoi.
   */
  updateAIRuntime(
    expectedRevision: number,
    desired: {
      profile: string;
      reasoning_allowed: boolean;
      concurrency_mode: "auto" | "manual";
      max_concurrent_text?: number;
      max_concurrent_speech?: number;
      max_queue?: number;
    },
  ) {
    return this.client.request<AIRuntimeView>(
      "/api/control/organization/ai-runtime",
      { method: "PUT", body: { expected_revision: expectedRevision, ...desired } },
    );
  }

  connectors() {
    return this.client.request<ConnectorListView>("/api/control/connectors");
  }

  createConnectorEnrollment(body: { display_name: string; deployment_id: string }) {
    return this.client.request<ConnectorEnrollmentView>(
      "/api/control/connectors/enrollments",
      { method: "POST", body },
    );
  }

  /**
   * Demande une rotation. La reponse ne porte aucun secret.
   *
   * L'administrateur pose un drapeau ; c'est le Connector qui conduit la
   * bascule et qui recevra le nouveau credential. Faire transiter un secret
   * durable par un navigateur en ferait la piece la plus exposee de la chaine.
   */
  requestConnectorRotation(connectorId: string) {
    return this.client.request<ConnectorView>(
      `/api/control/connectors/${connectorId}/rotate`,
      { method: "POST", body: {} },
    );
  }

  /**
   * Demande une mise a jour. Une version, et rien d'autre.
   *
   * Pas de commande, pas de chemin, pas d'argument : le Connector sait comment
   * se mettre a jour, et un canal capable de le lui dicter serait un shell
   * distant sous un autre nom.
   */
  requestConnectorUpdate(connectorId: string, targetVersion: string) {
    return this.client.request<ConnectorView>(
      `/api/control/connectors/${connectorId}/update`,
      { method: "POST", body: { target_version: targetVersion } },
    );
  }

  revokeConnector(connectorId: string) {
    return this.client.request<ConnectorView>(
      `/api/control/connectors/${connectorId}/revoke`,
      { method: "POST", body: {} },
    );
  }

  packages(organizationId: string) {
    return this.client.request<{ packages: PackageView[] }>(
      `/api/control/organizations/${organizationId}/packages`,
    );
  }

  createPackage(organizationId: string, body: Record<string, unknown>) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages`,
      { method: "POST", body },
    );
  }

  packageVersion(organizationId: string, packageId: string, versionId: string) {
    return this.client.request<PackageVersionDetail>(
      `/api/control/organizations/${organizationId}/packages/${packageId}` +
        `/versions/${versionId}`,
    );
  }

  createPackageDraft(
    organizationId: string,
    packageId: string,
    body: Record<string, unknown>,
  ) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}/versions`,
      { method: "POST", body },
    );
  }

  /** `expected_revision` obligatoire : deux consoles ne s'ecrasent pas. */
  updatePackageDraft(
    organizationId: string,
    packageId: string,
    versionId: string,
    body: { expected_revision: number; content: Record<string, unknown> },
  ) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}` +
        `/versions/${versionId}`,
      { method: "PUT", body },
    );
  }

  discardPackageDraft(
    organizationId: string,
    packageId: string,
    versionId: string,
  ) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}` +
        `/versions/${versionId}`,
      { method: "DELETE" },
    );
  }

  publishPackageVersion(
    organizationId: string,
    packageId: string,
    versionId: string,
  ) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}` +
        `/versions/${versionId}/publish`,
      { method: "POST", body: { activate: true } },
    );
  }

  /** Activer une version anterieure **est** le retour arriere. */
  activatePackageVersion(
    organizationId: string,
    packageId: string,
    versionId: string,
  ) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}` +
        `/versions/${versionId}/activate`,
      { method: "POST", body: {} },
    );
  }

  /** Sortir de l'archive. Le package repart en brouillon, pas en distribution. */
  restorePackage(organizationId: string, packageId: string) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}/restore`,
      { method: "POST", body: {} },
    );
  }

  archivePackage(organizationId: string, packageId: string) {
    return this.client.request<PackageView>(
      `/api/control/organizations/${organizationId}/packages/${packageId}/archive`,
      { method: "POST", body: {} },
    );
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
