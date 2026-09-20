/**
 * Appels du Control Plane utilisés par la console.
 *
 * Types explicites plutôt que `any` : quand le serveur changera, c'est le
 * compilateur qui le dira, pas un écran vide en production.
 */

import { ApiClient } from "./api";
import type {
  CustomLessonDraft,
  CustomLessonView,
  LearningAdminView,
  LearningLessonView,
  NovaUpdateProposal,
} from "./learning";

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

export interface ScimCredentialView {
  credential_id: string;
  name: string;
  provider_context: string;
  created_at: number;
  last_used_at: number | null;
  revoked_at: number | null;
  expires_at: number | null;
}

export interface ProvisioningView {
  endpoint: string;
  status: "active" | "not_configured";
  credentials: ScimCredentialView[];
  users_managed: number;
  groups_managed: number;
  managed_groups: {
    group_id: string;
    external_id: string | null;
    display_name: string;
    updated_at: number;
    member_count: number;
  }[];
  last_request: {
    created_at: number;
    action: string;
    success: number;
    error_code: string | null;
  } | null;
  last_success: { created_at: number; action: string } | null;
  last_error: { created_at: number; error_code: string } | null;
}

export interface DeploymentProfileView {
  profile_id: string;
  name: string;
  platform: string;
  edition: string;
  channel: "stable" | "preview";
  deployment_ring: string | null;
  update_policy: "manual" | "notify" | "automatic";
  autostart: boolean | null;
  config_revision: number;
  status: "active" | "retired";
  created_at: number;
  updated_at: number;
}

export interface DesktopReleaseView {
  version: string;
  edition: string;
  platform: string;
  arch: string;
  artifact: string;
  sha256: string;
  size: number;
  released_at: number;
  channel: string;
  minimum_supported_version: string;
}

export interface DeploymentView {
  status:
    | "not_configured"
    | "package_ready"
    | "configuration_error";
  platform: string;
  edition: string;
  installer_filename: string;
  control_plane_origin: string;
  profiles: DeploymentProfileView[];
  install_commands: Record<string, string>;
  release: {
    configured: boolean;
    signed: boolean;
    error: string | null;
    channels: Record<string, DesktopReleaseView | null>;
  };
  /** Aucun poste ne rapporte son etat : la console ne l'invente pas. */
  device_reporting: boolean;
}

export interface ManagedConfigView {
  filename: string;
  path: string;
  config: Record<string, unknown>;
  install_command: string;
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
  /** Des interrupteurs, une duree (`max_dictation_seconds`) et la liste des Styles desactives (`disabled_style_ids`). */
  settings: Record<string, boolean | number | string[]>;
  updated_at: number | null;
  updated_by_user_id: string | null;
  /** `false` tant que personne n'a rien change : ce sont les defauts. */
  configured: boolean;
  /** Les valeurs par defaut du produit, pour « revenir aux reglages par defaut ». */
  defaults: Record<string, boolean | number | string[]>;
}

/**
 * Les reglages qu'un groupe surcharge pour ses membres. `overrides` ne porte
 * que les choix faits pour ce groupe ; le reste suit l'organisation.
 */
export interface GroupPolicyView {
  id: string;
  label: string;
  overrides: Record<string, boolean | number>;
  revision: number;
  updated_at: number | null;
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

/** Un groupe, de la console ou de l'annuaire. Seuls les premiers se modifient ici. */
export interface GroupView {
  id: string;
  label: string;
  source: "console" | "scim" | string;
  external_id: string | null;
  member_count: number;
  /** Faux : le groupe n'apparaît chez aucun de ses membres sur Nova Desktop. */
  visible_to_members: boolean;
  updated_at: number;
}

export interface GroupRuleView {
  id: string;
  group_id: string;
  group_label: string;
  field: string;
  operator: string;
  value: string;
  enabled: boolean;
  updated_at: number;
}

/** Les règles, et ce que le serveur accepte comme champ et comme opérateur. */
export interface GroupRulesView {
  rules: GroupRuleView[];
  fields: string[];
  operators: string[];
}

export interface GroupRuleBody {
  group_id?: string;
  field: string;
  operator: string;
  value: string;
  enabled?: boolean;
}

/** Ce qu'un tri a changé : personne n'a à deviner. */
export interface RuleOutcome {
  added: number;
  removed: number;
}

/** Le choix fait pour une personne (`null` : elle suit ses réglages) et son effet. */
export interface MemberGroupDisplay {
  visible: boolean | null;
  effective: boolean;
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

  provisioning(organizationId: string) {
    return this.client.request<ProvisioningView>(
      `/api/control/organizations/${organizationId}/provisioning`,
    );
  }

  createScimCredential(
    organizationId: string,
    body: { name: string; provider_context: string },
  ) {
    return this.client.request<{
      credential: ScimCredentialView;
      /** Plaintext is present on this response only and stays in component memory. */
      token: string;
    }>(`/api/control/organizations/${organizationId}/provisioning/credentials`, {
      method: "POST",
      body,
    });
  }

  revokeScimCredential(organizationId: string, credentialId: string) {
    return this.client.request<{ revoked: boolean }>(
      `/api/control/organizations/${organizationId}/provisioning/credentials/${credentialId}/revoke`,
      { method: "POST", body: {} },
    );
  }

  deployment(organizationId: string) {
    return this.client.request<DeploymentView>(
      `/api/control/organizations/${organizationId}/deployment`,
    );
  }

  createDeploymentProfile(
    organizationId: string,
    body: {
      name: string;
      channel?: string;
      update_policy?: string;
      deployment_ring?: string | null;
    },
  ) {
    return this.client.request<{
      profile: DeploymentProfileView;
      install_command: string;
    }>(`/api/control/organizations/${organizationId}/deployment/profiles`, {
      method: "POST",
      body,
    });
  }

  updateDeploymentProfile(
    organizationId: string,
    profileId: string,
    body: { channel?: string; update_policy?: string; status?: string },
  ) {
    return this.client.request<{ profile: DeploymentProfileView }>(
      `/api/control/organizations/${organizationId}/deployment/profiles/${profileId}`,
      { method: "PUT", body },
    );
  }

  deploymentProfileConfig(organizationId: string, profileId: string) {
    return this.client.request<ManagedConfigView>(
      `/api/control/organizations/${organizationId}/deployment/profiles/${profileId}/config`,
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

  /** Ajoute un fournisseur d'identité. Le secret, s'il y en a un, ne revient jamais. */
  createProviderConfig(body: Record<string, unknown>) {
    return this.client.request<ProviderConfigView>("/api/admin/provider-configs", {
      method: "POST",
      body,
    });
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
  updatePolicy(expectedRevision: number, settings: Record<string, boolean | number | string[]>) {
    return this.client.request<PolicyView>("/api/control/organization/policy", {
      method: "PUT",
      body: { expected_revision: expectedRevision, settings },
    });
  }

  /** Chaque groupe de l'organisation, avec les reglages qu'il surcharge. */
  groupPolicies() {
    return this.client.request<{
      groups: GroupPolicyView[];
      defaults: Record<string, boolean | number>;
    }>("/api/control/organization/policy/groups");
  }

  /**
   * Remplace les surcharges d'un groupe ; un document vide le rend a
   * l'organisation. `expectedRevision` : deux consoles ne s'ecrasent pas.
   */
  updateGroupPolicy(
    groupId: string,
    expectedRevision: number,
    settings: Record<string, boolean | number>,
  ) {
    return this.client.request<GroupPolicyView>(
      `/api/control/organization/policy/groups/${encodeURIComponent(groupId)}`,
      { method: "PUT", body: { expected_revision: expectedRevision, settings } },
    );
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

  /** Définitif, et refusé tant que le package n'est pas archivé. */
  deletePackage(organizationId: string, packageId: string) {
    return this.client.request<void>(
      `/api/control/organizations/${organizationId}/packages/${packageId}`,
      { method: "DELETE" },
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

  groupRules(organizationId: string) {
    return this.client.request<GroupRulesView>(
      `/api/control/organizations/${organizationId}/group-rules`,
    );
  }

  createGroupRule(organizationId: string, body: GroupRuleBody) {
    return this.client.request<{ rule: GroupRuleView; applied: RuleOutcome }>(
      `/api/control/organizations/${organizationId}/group-rules`,
      { method: "POST", body },
    );
  }

  updateGroupRule(organizationId: string, ruleId: string, body: GroupRuleBody) {
    return this.client.request<{ rule: GroupRuleView; applied: RuleOutcome }>(
      `/api/control/organizations/${organizationId}/group-rules/${encodeURIComponent(ruleId)}`,
      { method: "PUT", body },
    );
  }

  /** La règle part, et ce qu'elle avait rangé aussi — jamais un ajout manuel. */
  deleteGroupRule(organizationId: string, ruleId: string) {
    return this.client.request<{ applied: RuleOutcome }>(
      `/api/control/organizations/${organizationId}/group-rules/${encodeURIComponent(ruleId)}`,
      { method: "DELETE" },
    );
  }

  /** Qui serait rangé, sans rien écrire. */
  previewGroupRule(organizationId: string, body: GroupRuleBody) {
    return this.client.request<{ count: number; sample: string[] }>(
      `/api/control/organizations/${organizationId}/group-rules/preview`,
      { method: "POST", body },
    );
  }

  applyGroupRules(organizationId: string) {
    return this.client.request<{ applied: RuleOutcome }>(
      `/api/control/organizations/${organizationId}/group-rules/apply`,
      { method: "POST", body: {} },
    );
  }

  memberGroupDisplay(organizationId: string, userId: string) {
    return this.client.request<MemberGroupDisplay>(
      `/api/control/organizations/${organizationId}/members/${encodeURIComponent(userId)}/group-display`,
    );
  }

  /** `null` rend la personne aux réglages de ses groupes et de l'organisation. */
  setMemberGroupDisplay(organizationId: string, userId: string, visible: boolean | null) {
    return this.client.request<MemberGroupDisplay>(
      `/api/control/organizations/${organizationId}/members/${encodeURIComponent(userId)}/group-display`,
      { method: "PUT", body: { visible } },
    );
  }

  /** Prépare un compte avant la première connexion. Aucun privilège n'y passe. */
  createMember(
    organizationId: string,
    body: { email: string; member_role?: string; cohort?: string },
  ) {
    return this.client.request<{ member: MemberView }>(
      `/api/control/organizations/${organizationId}/members`,
      { method: "POST", body },
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

  /** Chaque leçon avec ses réglages, les groupes, les modules et les langues. */
  learning() {
    return this.client.request<LearningAdminView>("/api/control/organization/learning");
  }

  /**
   * Obligatoire, échéance, groupes visés — pour une leçon livrée comme pour
   * une leçon de l'organisation. `expectedRevision` est la révision des
   * réglages affichés : deux consoles ouvertes ne s'écrasent pas.
   */
  updateLearningLesson(
    lessonId: string,
    expectedRevision: number,
    settings: { required?: boolean; due_at?: number | null; group_ids?: string[] },
  ) {
    return this.client.request<LearningLessonView>(
      `/api/control/organization/learning/lessons/${encodeURIComponent(lessonId)}`,
      { method: "PUT", body: { expected_revision: expectedRevision, ...settings } },
    );
  }

  /** Retire la leçon des membres. Leur progression est gardée. */
  archiveLearningLesson(lessonId: string, expectedRevision: number) {
    return this.client.request<LearningLessonView>(
      `/api/control/organization/learning/lessons/${encodeURIComponent(lessonId)}/archive`,
      { method: "POST", body: { expected_revision: expectedRevision } },
    );
  }

  restoreLearningLesson(lessonId: string, expectedRevision: number) {
    return this.client.request<LearningLessonView>(
      `/api/control/organization/learning/lessons/${encodeURIComponent(lessonId)}/restore`,
      { method: "POST", body: { expected_revision: expectedRevision } },
    );
  }

  /** Une leçon de l'organisation, avec toutes ses langues. */
  customLesson(lessonId: string) {
    return this.client.request<CustomLessonView>(
      `/api/control/organization/learning/custom-lessons/${encodeURIComponent(lessonId)}`,
    );
  }

  createCustomLesson(draft: CustomLessonDraft) {
    return this.client.request<CustomLessonView>(
      "/api/control/organization/learning/custom-lessons",
      { method: "POST", body: draft },
    );
  }

  /** `translations`, quand il est envoyé, remplace toutes les langues. */
  updateCustomLesson(
    lessonId: string,
    expectedRevision: number,
    draft: CustomLessonDraft,
  ) {
    return this.client.request<CustomLessonView>(
      `/api/control/organization/learning/custom-lessons/${encodeURIComponent(lessonId)}`,
      { method: "PUT", body: { expected_revision: expectedRevision, ...draft } },
    );
  }

  /** Définitif : la leçon, ses réglages et la progression des membres. */
  deleteCustomLesson(lessonId: string, expectedRevision: number) {
    return this.client.request<void>(
      `/api/control/organization/learning/custom-lessons/${encodeURIComponent(lessonId)}` +
        `?expected_revision=${expectedRevision}`,
      { method: "DELETE" },
    );
  }

  /**
   * Copie une leçon livrée dans une leçon de l'organisation, dans toutes ses
   * langues. La copie remplace l'original pour les membres.
   */
  customizeLearningLesson(lessonId: string) {
    return this.client.request<CustomLessonView>(
      `/api/control/organization/learning/lessons/${encodeURIComponent(lessonId)}/customize`,
      { method: "POST", body: {} },
    );
  }

  /** La version plus récente que Nova a livrée pour l'original d'une copie. */
  novaUpdate(lessonId: string) {
    return this.client.request<NovaUpdateProposal>(
      `/api/control/organization/learning/custom-lessons/${encodeURIComponent(lessonId)}/nova-update`,
    );
  }

  /** Applique la version de Nova, ou garde celle de l'organisation. */
  decideNovaUpdate(
    lessonId: string,
    expectedRevision: number,
    decision: "apply" | "dismiss",
  ) {
    return this.client.request<CustomLessonView>(
      `/api/control/organization/learning/custom-lessons/${encodeURIComponent(lessonId)}/nova-update`,
      { method: "POST", body: { expected_revision: expectedRevision, decision } },
    );
  }

  groups(organizationId: string) {
    return this.client.request<{ groups: GroupView[] }>(
      `/api/control/organizations/${organizationId}/groups`,
    );
  }

  createGroup(organizationId: string, displayName: string) {
    return this.client.request<{ group: GroupView }>(
      `/api/control/organizations/${organizationId}/groups`,
      { method: "POST", body: { display_name: displayName } },
    );
  }

  renameGroup(organizationId: string, groupId: string, displayName: string) {
    return this.client.request<{ group: GroupView }>(
      `/api/control/organizations/${organizationId}/groups/${encodeURIComponent(groupId)}`,
      { method: "PUT", body: { display_name: displayName } },
    );
  }

  /** Le groupe et ses appartenances. Les réglages qui le visent cessent de s'appliquer. */
  deleteGroup(organizationId: string, groupId: string) {
    return this.client.request<void>(
      `/api/control/organizations/${organizationId}/groups/${encodeURIComponent(groupId)}`,
      { method: "DELETE" },
    );
  }

  /** Montre ou retire le groupe de l'affichage chez ses membres, annuaire compris. */
  setGroupVisibility(organizationId: string, groupId: string, visibleToMembers: boolean) {
    return this.client.request<{ group: GroupView }>(
      `/api/control/organizations/${organizationId}/groups/${encodeURIComponent(groupId)}/visibility`,
      { method: "PUT", body: { visible_to_members: visibleToMembers } },
    );
  }

  groupMembers(organizationId: string, groupId: string) {
    return this.client.request<{
      members: { user_id: string; email: string; origin: "manual" | "rule" }[];
    }>(
      `/api/control/organizations/${organizationId}/groups/${encodeURIComponent(groupId)}/members`,
    );
  }

  addGroupMember(organizationId: string, groupId: string, userId: string) {
    return this.client.request<{ group: GroupView }>(
      `/api/control/organizations/${organizationId}/groups/${encodeURIComponent(groupId)}` +
        `/members/${encodeURIComponent(userId)}`,
      { method: "PUT", body: {} },
    );
  }

  removeGroupMember(organizationId: string, groupId: string, userId: string) {
    return this.client.request<{ group: GroupView }>(
      `/api/control/organizations/${organizationId}/groups/${encodeURIComponent(groupId)}` +
        `/members/${encodeURIComponent(userId)}`,
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
