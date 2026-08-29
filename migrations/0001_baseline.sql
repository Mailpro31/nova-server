-- 0001 baseline_phase37
--
-- Etat complet du schema a la fin de la Phase 37, tel que init_db() et
-- migrate_schema() le produisaient. Ce n'est pas un historique reconstitue :
-- le schema n'en avait pas. C'est le point de depart declare, et les
-- migrations suivantes s'y ajoutent normalement.
--
-- Dialecte : TEXT, REAL et INTEGER seulement. Aucun CHECK, COLLATE, ni
-- DEFAULT CURRENT_TIMESTAMP. Les horodatages sont des REAL epoch, donc
-- identiques sous les deux moteurs. Seul INTEGER PRIMARY KEY AUTOINCREMENT
-- differe, et migrations.py le traduit explicitement.

CREATE TABLE admin_audit_log(
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    actor_user_id TEXT,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    request_id TEXT,
    metadata TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE admin_sessions(
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    email TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    security_role TEXT NOT NULL,
    auth_provider TEXT,
    token_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL,
    revoked_at REAL
);

CREATE TABLE auth_codes(
    email TEXT, code TEXT, expires_at REAL, attempts INTEGER DEFAULT 0,
    organization_id TEXT
    , user_id TEXT);

CREATE TABLE connector_enrollments(
    enrollment_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    created_by_user_id TEXT,
    consumed_at REAL,
    consumed_by_connector_id TEXT
);

CREATE TABLE connector_releases(
    release_id TEXT PRIMARY KEY,
    connector_version TEXT NOT NULL UNIQUE,
    protocol_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    manifest TEXT NOT NULL,
    signature TEXT NOT NULL,
    released_at REAL NOT NULL,
    created_by_user_id TEXT,
    revoked_at REAL
);

CREATE TABLE connectors(
    connector_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    deployment_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    credential_hash TEXT NOT NULL,
    protocol_version INTEGER NOT NULL DEFAULT 0,
    connector_version TEXT,
    server_version TEXT,
    capabilities TEXT NOT NULL DEFAULT '[]',
    runtime_state TEXT,
    delivered_revision INTEGER NOT NULL DEFAULT 0,
    applied_revision INTEGER NOT NULL DEFAULT 0,
    apply_status TEXT NOT NULL DEFAULT 'pending',
    last_error_code TEXT,
    created_at REAL NOT NULL,
    last_seen_at REAL,
    revoked_at REAL,
    credential_generation INTEGER NOT NULL DEFAULT 1,
    credential_created_at REAL,
    pending_credential_hash TEXT,
    pending_created_at REAL,
    rotation_state TEXT NOT NULL DEFAULT 'stable',
    rotation_requested_at REAL,
    last_rotated_at REAL
    , platform_os TEXT, platform_arch TEXT, distribution_mode TEXT, update_state TEXT NOT NULL DEFAULT 'up_to_date', update_policy TEXT NOT NULL DEFAULT 'manual', target_version TEXT, last_known_good_version TEXT, last_update_at REAL, last_update_error TEXT);

CREATE UNIQUE INDEX idx_connectors_credential
    ON connectors(credential_hash);

CREATE TABLE federated_identities(
    user_id TEXT,
    provider TEXT NOT NULL,
    external_subject TEXT NOT NULL,
    external_tenant_id TEXT,
    organization_id TEXT NOT NULL,
    email TEXT NOT NULL,
    verification TEXT NOT NULL DEFAULT 'transport_only',
    external_issuer TEXT NOT NULL DEFAULT '',
    created_at REAL,
    last_seen REAL,
    PRIMARY KEY (organization_id, provider, external_issuer, external_subject)
);

CREATE TABLE formatting_rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT,
    rule TEXT NOT NULL,
    shared INTEGER NOT NULL DEFAULT 0
    , user_id TEXT, organization_id TEXT);

CREATE TABLE organization_ai_runtime_config(
    organization_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    desired_revision INTEGER NOT NULL,
    desired_profile TEXT NOT NULL,
    reasoning_allowed INTEGER NOT NULL,
    max_concurrent_text INTEGER NOT NULL,
    max_concurrent_speech INTEGER NOT NULL,
    max_queue INTEGER NOT NULL,
    applied_revision INTEGER NOT NULL DEFAULT 0,
    apply_status TEXT NOT NULL DEFAULT 'pending',
    updated_at REAL NOT NULL,
    updated_by_user_id TEXT
    , delivered_revision INTEGER NOT NULL DEFAULT 0, delivered_at REAL, applied_at REAL, last_error_code TEXT);

CREATE TABLE organization_package_versions(
    version_id         TEXT PRIMARY KEY,
    package_id         TEXT NOT NULL,
    organization_id    TEXT NOT NULL,
    version_number     INTEGER NOT NULL,
    schema_version     INTEGER NOT NULL,
    content            TEXT NOT NULL,
    content_hash       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'draft',
    validation_status  TEXT NOT NULL DEFAULT 'unknown',
    validation_error   TEXT,
    revision           INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL,
    created_by_user_id TEXT,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT,
    published_at       REAL,
    UNIQUE(package_id, version_number)
);

CREATE TABLE organization_packages(
    package_id         TEXT PRIMARY KEY,
    organization_id    TEXT NOT NULL,
    package_type       TEXT NOT NULL,
    name               TEXT NOT NULL,
    description        TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'draft',
    active_version_id  TEXT,
    revision           INTEGER NOT NULL DEFAULT 0,
    created_at         REAL NOT NULL,
    created_by_user_id TEXT,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT
);

CREATE TABLE organization_policies(
    organization_id    TEXT PRIMARY KEY,
    schema_version     INTEGER NOT NULL,
    revision           INTEGER NOT NULL,
    settings           TEXT NOT NULL,
    updated_at         REAL NOT NULL,
    updated_by_user_id TEXT
);

CREATE TABLE organization_provider_configs(
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    provider_type TEXT NOT NULL,
    display_name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    entra_tenant_id TEXT,
    google_hosted_domain TEXT,
    oidc_issuer TEXT,
    oidc_client_id TEXT,
    oidc_client_secret TEXT,
    oidc_client_auth_method TEXT DEFAULT 'none',
    oidc_allow_private_issuer INTEGER NOT NULL DEFAULT 0,
    oidc_required_claim TEXT,
    oidc_required_claim_value TEXT,
    created_at REAL,
    updated_at REAL,
    created_by TEXT,
    updated_by TEXT
);

CREATE UNIQUE INDEX idx_provider_configs_issuer
    ON organization_provider_configs(organization_id, provider_type, oidc_issuer);

CREATE TABLE organizations(
    id TEXT PRIMARY KEY,
    slug TEXT,
    display_name TEXT NOT NULL,
    org_type TEXT NOT NULL DEFAULT 'education',
    entra_tenant_id TEXT,
    google_customer_id TEXT,
    google_hosted_domain TEXT,
    oidc_issuer TEXT,
    discovery_slug TEXT,
    discovery_enabled INTEGER NOT NULL DEFAULT 0,
    deployment_mode TEXT NOT NULL DEFAULT 'dedicated',
    service_endpoint TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL
);

CREATE UNIQUE INDEX idx_organizations_slug ON organizations(discovery_slug);

CREATE TABLE personal_dictionary(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    term TEXT NOT NULL,
    replacement TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'personal', user_id TEXT, organization_id TEXT,
    UNIQUE(email, term)
);

CREATE TABLE server_state(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE shared_dictionary(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL UNIQUE,
    replacement TEXT NOT NULL
);

CREATE TABLE snippets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    trigger TEXT NOT NULL,
    content TEXT NOT NULL, user_id TEXT, organization_id TEXT,
    UNIQUE(email, trigger)
);

CREATE TABLE sso_flows(
    flow_id_hash TEXT PRIMARY KEY,
    provider_type TEXT NOT NULL,
    provider_config_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    nonce_hash TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    machine TEXT,
    purpose TEXT NOT NULL DEFAULT 'login',
    expected_user_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    exchanging_at REAL,
    consumed_at REAL
);

CREATE TABLE tokens(
    token TEXT PRIMARY KEY, email TEXT, machine TEXT,
    user_id TEXT, organization_id TEXT,
    active INTEGER DEFAULT 1, created_at REAL, expires_at REAL
    , token_hash TEXT);

CREATE UNIQUE INDEX idx_tokens_hash ON tokens(token_hash);

CREATE TABLE usage(
    email TEXT, day TEXT,
    transcriptions INTEGER DEFAULT 0,
    reformulations INTEGER DEFAULT 0,
    audio_seconds REAL DEFAULT 0, user_id TEXT, organization_id TEXT,
    PRIMARY KEY (email, day)
);

CREATE TABLE users(
    user_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL,
    role TEXT DEFAULT 'student',
    security_role TEXT NOT NULL DEFAULT 'member',
    cohort TEXT DEFAULT '',
    note TEXT DEFAULT '',
    disabled INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    identity_provider TEXT,
    external_subject TEXT,
    external_tenant_id TEXT,
    created_at REAL,
    updated_at REAL,
    last_seen REAL
);

CREATE UNIQUE INDEX idx_users_org_email ON users(organization_id, normalized_email);

CREATE UNIQUE INDEX idx_users_org_user_id
    ON users(organization_id, user_id);

CREATE TABLE deployment_profiles(
    profile_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    edition TEXT NOT NULL,
    channel TEXT NOT NULL,
    deployment_ring TEXT,
    update_policy TEXT NOT NULL,
    autostart INTEGER,
    config_revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    created_by_user_id TEXT,
    UNIQUE (organization_id, name),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE organization_groups(
    organization_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    external_id TEXT,
    display_name TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    PRIMARY KEY (organization_id, group_id),
    UNIQUE (organization_id, external_id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE scim_activity(
    activity_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    credential_id TEXT,
    action TEXT NOT NULL,
    success INTEGER NOT NULL,
    error_code TEXT,
    target_type TEXT,
    target_id TEXT,
    created_at REAL NOT NULL,
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE scim_credentials(
    credential_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    provider_context TEXT NOT NULL DEFAULT 'generic',
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    last_used_at REAL,
    revoked_at REAL,
    expires_at REAL,
    created_by_user_id TEXT,
    FOREIGN KEY (organization_id) REFERENCES organizations(id)
);

CREATE TABLE scim_user_management(
    organization_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    external_id TEXT,
    user_name TEXT NOT NULL,
    normalized_user_name TEXT NOT NULL,
    given_name TEXT NOT NULL DEFAULT '',
    family_name TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    department TEXT,
    enterprise_organization TEXT,
    manager_value TEXT,
    employee_number TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_provisioned_at REAL NOT NULL,
    deprovisioned_at REAL,
    PRIMARY KEY (organization_id, user_id),
    UNIQUE (organization_id, external_id),
    UNIQUE (organization_id, normalized_user_name),
    FOREIGN KEY (organization_id, user_id)
    REFERENCES users(organization_id, user_id)
);

CREATE TABLE organization_group_memberships(
    organization_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (organization_id, group_id, user_id),
    FOREIGN KEY (organization_id, group_id)
    REFERENCES organization_groups(organization_id, group_id)
    ON DELETE CASCADE,
    FOREIGN KEY (organization_id, user_id)
    REFERENCES users(organization_id, user_id) ON DELETE CASCADE
);

CREATE INDEX idx_admin_audit_org
    ON admin_audit_log(organization_id, created_at);
CREATE INDEX idx_admin_sessions_user
    ON admin_sessions(user_id, revoked_at);
CREATE INDEX idx_auth_codes_user ON auth_codes(user_id);
CREATE INDEX idx_connectors_org
    ON connectors(organization_id);
CREATE INDEX idx_deployment_profiles_org
    ON deployment_profiles(organization_id, status);
CREATE INDEX idx_federated_identities_user ON federated_identities(user_id);
CREATE INDEX idx_formatting_rules_user ON formatting_rules(user_id);
CREATE INDEX idx_package_versions_package
    ON organization_package_versions(package_id, version_number);
CREATE INDEX idx_packages_org
    ON organization_packages(organization_id, package_type);
CREATE INDEX idx_personal_dictionary_user ON personal_dictionary(user_id);
CREATE INDEX idx_provider_configs_org
    ON organization_provider_configs(organization_id, provider_type);
CREATE INDEX idx_scim_activity_org
    ON scim_activity(organization_id, created_at);
CREATE INDEX idx_scim_credentials_org
    ON scim_credentials(organization_id, revoked_at);
CREATE INDEX idx_scim_group_members_user
    ON organization_group_memberships(organization_id, user_id);
CREATE INDEX idx_scim_groups_name
    ON organization_groups(organization_id, display_name);
CREATE INDEX idx_scim_users_external
    ON scim_user_management(organization_id, external_id);
CREATE INDEX idx_snippets_user ON snippets(user_id);
CREATE INDEX idx_sso_flows_expiry ON sso_flows(expires_at);
CREATE INDEX idx_tokens_user ON tokens(user_id);
CREATE INDEX idx_usage_user ON usage(user_id);
