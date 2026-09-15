/**
 * Textes de l'interface.
 *
 * ## Pourquoi l'anglais, et pourquoi un seul fichier
 *
 * Nova Desktop porte 21 langues, avec un contrôle de complétude. Les dupliquer
 * pour une console qui n'a pas encore d'utilisateur reviendrait à faire traduire
 * 21 fois des libellés qui vont bouger à chaque phase.
 *
 * Nova Admin commence donc en anglais — la langue de fait des consoles
 * d'administration — avec **toutes** ses chaînes rassemblées ici. Le jour où la
 * traduction sera utile, il n'y aura pas à parcourir les composants : ce module
 * devient le catalogue par défaut, et rien d'autre ne change.
 *
 * Aucune chaîne visible ne doit être écrite en dur dans un composant.
 */

import type { ApiErrorKind } from "./api";

export const t = {
  product: "Nova Admin",

  nav: {
    overview: "Overview",
    identity: "Identity",
    provisioning: "Provisioning",
    administrators: "Administrators",
    members: "Members",
    learn: "Learn",
    packages: "Packages",
    deployment: "Deployment",
    configuration: "Configuration",
    security: "Security",
    diagnostics: "Diagnostics",
  },

  bootstrap: {
    title: "Nova Admin",
    prompt: "Enter your organization identifier",
    hint: "Your IT department provides this. It is not a password.",
    field: "Organization",
    submit: "Continue",
    looking: "Looking up organization…",
  },

  signIn: {
    title: "Sign in to manage",
    lead: "Nova Admin uses your organization's identity provider. There is no Nova password.",
    microsoft: "Continue with Microsoft",
    google: "Continue with Google",
    oidc: "Continue with company SSO",
    none: "No sign-in method is configured for this organization.",
  },

  stepUp: {
    title: "Re-authenticate to continue",
    lead: "Administering an organization requires a recent sign-in. Your identity provider will ask you to authenticate again.",
    note: "Nova does not add a second factor of its own. Your provider decides what to ask for.",
    action: "Continue",
    working: "Waiting for your identity provider…",
  },

  denied: {
    title: "Access denied",
    lead: "This account cannot administer this organization.",
    hint: "If this is unexpected, contact whoever manages Nova for your organization.",
  },

  session: {
    role: "Role",
    expiresIn: "Session expires in",
    signOut: "Sign out",
    signOutAll: "Sign out completely",
  },

  state: {
    loading: "Loading…",
    empty: "Nothing to show yet.",
    retry: "Try again",
    notConfigured: "Not configured yet",
    unavailable: "Unavailable",
  },

  overview: {
    title: "Overview",
    organization: "Organization",
    status: "Status",
    serverMode: "Server mode",
    deploymentMode: "Deployment mode",
    discovery: "Organization discovery",
    providers: "Sign-in providers",
    seats: "Seats in use",
    yourAccess: "Your access",
    enabledOf: (enabled: number, total: number) => `${enabled} of ${total} enabled`,
  },

  identity: {
    title: "Identity",
    lead: "Identity providers that can sign people in to this organization.",
    type: "Type",
    status: "Status",
    enabled: "Enabled",
    disabled: "Disabled",
    secret: "Client secret",
    secretConfigured: "Configured",
    secretAbsent: "Not set",
    replaceSecret: "Replace secret",
    newSecret: "New client secret",
    save: "Save changes",
    disable: "Disable provider",
    disableTitle: "Disable this provider?",
    disableBody:
      "People who sign in with this provider will no longer be able to. Existing sessions are unaffected.",
    disableConfirm: "Disable",
    cancel: "Cancel",
    broken: "Configuration incomplete",
    lastActive:
      "This is the only enabled provider. Disabling it would lock every administrator out, so it is refused. Add another provider first.",
  },

  provisioning: {
    title: "Identity / Provisioning",
    lead: "Automate organization membership with a tenant-scoped SCIM 2.0 connection.",
    endpoint: "Tenant URL",
    status: "SCIM status",
    active: "Active",
    notConfigured: "Not configured",
    managedUsers: "Users managed by SCIM",
    managedGroups: "Groups managed by SCIM",
    lastRequest: "Last request",
    lastSuccess: "Last successful provisioning",
    lastError: "Last safe error",
    never: "Never",
    credentials: "Credentials",
    credentialName: "Credential name",
    providerContext: "Reconciliation context",
    genericProvider: "Generic SCIM client",
    entraProvider: "Microsoft Entra ID",
    googleProvider: "Google Workspace",
    oidcProvider: "Generic OIDC provider",
    generate: "Generate credential",
    generating: "Generating…",
    generatedTitle: "Secret token — shown once",
    generatedBody:
      "Copy this token now. Nova stores only its fingerprint and cannot display it again.",
    copied: "Copied.",
    copy: "Copy token",
    done: "I have saved it",
    created: "Created",
    lastUsed: "Last used",
    revoked: "Revoked",
    expires: "Expires",
    revoke: "Revoke",
    revokeTitle: "Revoke this SCIM credential?",
    revokeBody:
      "The provisioning client using it will be refused immediately. Other active credentials keep working.",
    revokeConfirm: "Revoke credential",
    readOnly: "Your role can inspect provisioning but cannot generate or revoke credentials.",
    groupsTitle: "SCIM-managed groups",
    group: "Group",
    members: "Members",
    updated: "Updated",
    noGroups: "No group has been provisioned yet.",
    entraTitle: "Configure Microsoft Entra provisioning",
    entraSteps: [
      "Create or open the controlled Enterprise application in Microsoft Entra.",
      "Under Provisioning, choose Automatic and paste the Tenant URL and Secret Token.",
      "Test Connection, then scope assignment to one controlled test user or test group only.",
      "Start provisioning and verify create, update, disable and re-enable in Nova Admin.",
    ],
    entraWarning:
      "Do not enter a Microsoft password or an Entra client secret here. Inbound SCIM uses only the Nova Secret Token.",
  },

  administrators: {
    title: "Administrators",
    lead: "Who can administer this organization, and what each of them may do.",
    person: "Person",
    role: "Role",
    status: "Status",
    none: "No administrator yet.",
    // Le premier administrateur ne nait pas d'un appel HTTP : le dire evite de
    // chercher un bouton qui n'existera jamais.
    bootstrapNote:
      "The first administrator is granted on the server, from the command line. Nova Admin manages the ones that follow.",
    roleOrganizationAdmin: "Organization administrator",
    roleItAdmin: "IT administrator",
    roleReadOnly: "Read only",
    roleContentEditor: "Content editor",
    roleMember: "No admin access",
    roleOrganizationAdminHint: "Manages people: administrators, providers, discovery, security.",
    roleItAdminHint: "Manages the service: providers, discovery, deployment.",
    roleReadOnlyHint: "Reads the console. Changes nothing.",
    roleContentEditorHint:
      "Manages content: Learn lessons, Styles, Prompts and vocabulary. No access to members, security or policies.",
    roleMemberHint: "Uses Nova, administers nothing.",
    changeRole: "Change role",
    removeAccess: "Remove admin access",
    removeTitle: "Remove administrator access?",
    removeBody:
      "This person keeps their Nova account and can still dictate. They simply stop administering, and their administration sessions end immediately.",
    removeConfirm: "Remove access",
    changeTitle: "Change this administrator's role?",
    changeBody:
      "Their open administration sessions end immediately. They will re-authenticate to continue, with their new capabilities.",
    changeConfirm: "Change role",
    lastManager:
      "At least one organization administrator must remain. Grant the role to someone else first.",
    addTitle: "Grant administrator access",
    addLead: "Search for someone who already uses Nova in this organization.",
    searchLabel: "Email",
    searchPlaceholder: "Start typing an email",
    searchShort: "Type at least two characters.",
    searchEmpty: "No matching account.",
    grant: "Grant",
    grantedTo: (email: string) => `${email} is now an administrator.`,
  },

  members: {
    title: "Members",
    lead: "Who belongs to this organization, and what access they have.",
    // La frontiere, affichee plutot que seulement documentee : c'est ici qu'on
    // serait tente d'ajouter « ce que les gens ont dicte ».
    boundary:
      "Identity and access only. Nova Admin never shows what anyone dictated, typed or asked.",
    search: "Search by email",
    searchPlaceholder: "Start typing an email",
    filterAll: "All",
    filterActive: "Active",
    filterDisabled: "Suspended",
    none: "No member matches.",
    count: (shown: number, total: number) => `${shown} of ${total} members`,
    more: "Refine your search to see the rest.",
    memberType: "Type",
    groups: "Groups",
    noGroup: "None",
    devices: "Devices",
    noDevice: "No device signed in.",
    identities: "Sign-in methods",
    adminSessions: "Open administration sessions",
    note: "Administrative note",
    open: "Open",
    back: "Back to members",
    suspend: "Suspend account",
    reactivate: "Reactivate account",
    suspendTitle: "Suspend this account?",
    suspendBody:
      "They are signed out of every device immediately and cannot sign in again. Their account, identity and history are kept — this is not a deletion.",
    suspendConfirm: "Suspend",
    reactivateTitle: "Reactivate this account?",
    reactivateBody:
      "They will be able to sign in again. Previous sessions are not restored: they sign in fresh.",
    reactivateConfirm: "Reactivate",
    revokeSessions: "Sign out of all devices",
    revokeTitle: "Sign this person out everywhere?",
    revokeBody:
      "Every device is signed out. The account stays active and they can sign in again right away.",
    revokeConfirm: "Sign out everywhere",
    revoked: (devices: number) => `Signed out of ${devices} device(s).`,
    remove: "Delete account",
    removeTitle: "Delete this account permanently?",
    removeBody:
      "The account, its sessions, its personal dictionary and its sign-in methods are erased. The administration audit trail is kept — it records who did what, not what anyone dictated. Suspending is usually the better choice.",
    removeConfirm: "Delete permanently",
    lastManager:
      "At least one organization administrator must remain. This account cannot be suspended or deleted.",
    saveProfile: "Save",
    profileSaved: "Saved.",
  },

  deployment: {
    title: "Deployment",
    lead: "Where this organization's Nova service runs.",
    mode: "Deployment mode",
    endpoint: "Service endpoint",
    slug: "Discovery identifier",
    version: "Server version",
    fleetTitle: "Workstation fleet",
    fleetBody:
      "Deployed workstations do not report to Nova. This panel would need a device report channel that does not exist, so no fleet health is shown rather than guessed.",

    desktopTitle: "Windows deployment",
    desktopLead:
      "Prepare a per-machine package your IT department can install silently on every workstation.",
    packageStatus: "Package status",
    statusNotConfigured: "Not configured",
    statusPackageReady: "Package ready",
    statusConfigurationError: "Configuration error",
    installer: "Installer",
    origin: "Control plane origin",
    originNote:
      "Pinned in the package. A deployment policy selects a profile; it cannot point Nova at another address.",
    deviceReporting: "Workstation reporting",
    deviceReportingNone:
      "None. Installed workstations do not send Nova any state, so this page describes the package and its configuration, never the health of a machine.",
    releaseTitle: "Available release",
    releaseNone:
      "No release manifest is configured on this service, so no version is recommended. Nova will not name a version it cannot verify.",
    releaseUnsigned:
      "No trust root is configured for desktop releases. The manifest is served as-is and is not claimed to be signed.",
    releaseError: "The release manifest is configured but could not be trusted.",
    desktopVersion: "Version",
    minimumVersion: "Minimum supported version",
    artifact: "Artifact",
    digest: "SHA-256",
    signed: "Signature",
    signedYes: "Verified",
    signedNo: "Not verified",

    profilesTitle: "Deployment profiles",
    profileName: "Profile name",
    channel: "Release channel",
    channelStable: "Stable",
    channelPreview: "Preview",
    updatePolicy: "Update policy",
    policyManual: "Manual",
    policyNotify: "Notify",
    policyAutomatic: "Automatic",
    revision: "Configuration revision",
    profileStatus: "Status",
    profileActive: "Active",
    profileRetired: "Retired",
    create: "Create profile",
    creating: "Creating…",
    retire: "Retire",
    retireTitle: "Retire this deployment profile?",
    retireBody:
      "New installations using it will no longer be recognized. Workstations already installed keep working on the configuration they applied.",
    retireConfirm: "Retire profile",
    noProfiles:
      "No deployment profile yet. Create one to obtain an install command and a machine configuration file.",
    readOnly:
      "Your role can inspect the deployment but cannot change it. Deployment is managed by the IT administrator role.",

    installCommand: "Silent install command",
    installCommandNote:
      "Contains no durable secret. The deployment identifier authenticates nobody: every user still signs in through SSO.",
    managedConfig: "Machine configuration",
    managedConfigNote:
      "Write this to C:\ProgramData\Nova\organization.json, or let the installer write it from the command above.",
    copy: "Copy",
    copied: "Copied.",
  },

  connector: {
    title: "Private Connector",
    lead: "How this organization's deployment receives configuration from Nova.",
    privacy:
      "The connector synchronizes control metadata only — revisions, status, versions and health. Dictated audio, prompts, transcripts and model output never leave the organization's own infrastructure.",
    outbound:
      "The connector calls Nova; Nova never calls your network. No inbound port has to be opened, and no private address is known to Nova.",

    none: "No connector is enrolled yet. Until one is, configuration saved here waits instead of being applied.",
    pending: "enrollment code(s) waiting to be used",

    status: "Status",
    statusOnline: "Online",
    statusDegraded: "Degraded",
    statusOffline: "Offline",
    statusRevoked: "Revoked",
    lastSeen: "Last seen",
    never: "Never",
    deployment: "Deployment",
    connectorVersion: "Connector version",
    serverVersion: "Nova server version",
    protocol: "Protocol",
    protocolUnsupported: "Unsupported protocol — this connector needs updating before it can apply configuration.",
    updateRecommended: "A newer connector protocol is available. Updating is recommended; nothing breaks until then.",
    capabilities: "Capabilities",
    runtimeStatus: "AI runtime",
    deliveredRevision: "Delivered revision",
    appliedRevision: "Applied revision",
    applyStatus: "Configuration",
    lastError: "Last error",

    enrollTitle: "Enroll a connector",
    enrollBody:
      "Creates a single-use code. Run the command below on the organization's server; the code stops working once used, and after it expires.",
    enrollName: "Name",
    enrollDeployment: "Deployment identifier",
    enrollAction: "Create enrollment code",
    enrollCreating: "Creating…",
    enrollOnce: "This code is shown once. Nova keeps only its fingerprint and cannot show it again.",
    enrollExpires: "Expires in",
    enrollCommand: "Run on the organization's server",
    enrollDone: "Done",

    versionTitle: "Version and updates",
    // Ce champ dit un OS et une architecture. Le terme generique auquel on
    // penserait d'abord appartient au vocabulaire d'une console operateur
    // multi-tenant, qu'un garde de test empeche d'apparaitre ici — y compris
    // dans les commentaires, puisqu'il lit le fichier brut.
    runsOn: "Runs on",
    distribution: "Distribution",
    selfUpdate: "Self-update",
    selfUpdateYes: "Supported",
    selfUpdateNo: "Orchestrated by your deployment",
    selfUpdateUnknown: "Not reported — this connector predates update support",
    externalRequired: "Deployment-managed update required",
    externalBody:
      "A newer version is available. This connector runs as a container and cannot replace its own image, so Nova will not attempt it. Pull the new image and recreate the service the way you deploy it; the connector keeps its identity, its credential and its applied configuration across the replacement.",
    selfUpdateBody:
      "This connector runs as a container, and a container cannot replace its own image. Update it the way you deploy it \u2014 pull the new image and recreate the service. Nova will not offer a button that could not work.",
    installedVersion: "Installed",
    recommendedVersion: "Recommended",
    minimumVersion: "Minimum supported",
    supportStatus: "Support",
    supportUpToDate: "Up to date",
    supportUpdateAvailable: "Update available",
    supportUnsupported: "Too old \u2014 update recommended",
    supportUnknown: "Unknown",
    unsupportedBody:
      "This connector is older than the minimum supported version. It keeps running and keeps serving your deployment \u2014 disconnecting it would only deny it the update that brings it back into support.",
    updateState: "Update state",
    lastUpdate: "Last update",
    lastKnownGood: "Last known good",
    updateError: "Last update error",
    requestUpdate: "Update to",
    requestingUpdate: "Requesting\u2026",
    updateBody:
      "Nova sends a version, never a command. The connector fetches the release itself, verifies its signature and its digest, stages it, and only reports success once the new version is running and healthy. If it is not, it returns to the previous one.",
    neverUpdated: "Never",

    credentialTitle: "Credential",
    credentialAge: "Age",
    credentialGeneration: "Generation",
    lastRotated: "Last rotated",
    neverRotated: "Never rotated",
    rotationState: "Rotation",
    rotationStable: "Healthy",
    rotationRequested: "Requested — waiting for the connector",
    rotationPending: "Rotating — waiting for the connector to confirm",
    rotationFailed: "Last rotation did not complete. The current credential still works; you can request another.",
    rotationUnsupported:
      "This connector is too old to rotate its credential. Update it, or revoke and enroll it again.",
    rotationBody:
      "Rotation replaces the connector’s credential without re-enrolling it. Nova never shows the new secret — the connector receives it directly and proves it can use it before the old one stops working.",
    requestRotation: "Request rotation",
    requestingRotation: "Requesting…",
    rotationCompromised:
      "If you believe the credential is compromised, revoke the connector instead. Rotation assumes the old credential still works.",

    revoke: "Revoke",
    revoking: "Revoking…",
    revokeBody:
      "Revoking stops synchronization. It does not stop dictation: the deployment keeps running on the configuration it already applied.",
    readOnly: "Your role can read this page but not enroll or revoke a connector.",
  },

  aiRuntime: {
    title: "AI Runtime",
    lead: "Where this organization's AI processing actually runs.",
    privacy:
      "Dictation, transcripts, prompts and model output stay on this organization's infrastructure. Nova's control plane stores configuration and health only — never user content.",
    processing: "Processing location",
    onPremises: "Organization private infrastructure",
    cloudFallback: "Cloud fallback",
    cloudFallbackDisabled: "Disabled",
    cloudFallbackBody:
      "If the private runtime is down, requests fail with a clear error. Nova never silently sends content to an external AI provider.",

    statusTitle: "Runtime status",
    status: "Runtime",
    statusHealthy: "Healthy",
    statusDegraded: "Degraded",
    statusStarting: "Starting",
    statusUnavailable: "Unavailable",
    textEngine: "Text engine",
    speechEngine: "Speech engine",
    engineReady: "Ready",
    engineLoading: "Loading",
    engineUnavailable: "Unavailable",
    lastCheck: "Last health check",
    never: "Never",
    adapter: "Adapter",
    model: "Loaded model",
    runtimeVersion: "Runtime version",
    latency: "Health latency",
    lastError: "Last error",
    capacity: "In flight / queued",
    advanced: "Advanced details",

    hardwareTitle: "Hardware detected",
    cpu: "CPU",
    architecture: "Architecture",
    ram: "Memory",
    gpu: "GPU",
    vram: "GPU memory",
    driver: "Driver",
    noGpu: "No supported GPU detected",
    recommended: "Recommended profile",
    recommendedBody:
      "Based on the hardware reported by this server. Nova never applies it on its own — choosing the profile stays an administrator's decision.",

    configTitle: "Configuration",
    profile: "Model profile",
    profileEfficient: "Efficient — lowest latency and resource use",
    profileStandard: "Standard — balanced, recommended",
    profileAdvanced: "Advanced — highest quality and reasoning",
    profileCustom: "Custom — declared by this deployment",
    customUnavailable:
      "Custom is not offered by this deployment. It describes parameters declared in the server’s own configuration, so it only becomes selectable once your IT department has set them.",
    customTitle: "Custom profile parameters",
    customBody:
      "Declared in the deployment configuration and shown here for diagnosis. They are not editable from this console.",
    customTokens: "Maximum output length",
    customTemperature: "Temperature",
    customReasoning: "Reasoning",
    profileBody:
      "A profile describes the behaviour you want. Which model serves it belongs to the runtime configuration, not to this screen.",
    reasoning: "Allow enhanced reasoning",
    reasoningBody:
      "Lets longer tasks think before answering. Dictation and rewriting stay direct either way — latency matters more there.",
    concurrency: "Concurrency",
    concurrencyAuto: "Automatic",
    concurrencyManual: "Controlled",
    concurrencyText: "Concurrent text requests",
    concurrencySpeech: "Concurrent speech requests",
    concurrencyQueue: "Queue length",
    concurrencyBody:
      "When the runtime is saturated, Nova answers “busy” rather than letting requests hang until they time out.",

    stateTitle: "Desired and applied",
    desiredRevision: "Desired revision",
    appliedRevision: "Applied revision",
    applyStatus: "Apply status",
    pending: "Saved, not yet applied by the runtime",
    appliedLocal: "Applied by this server",
    localDefault: "Server defaults, never configured here",
    awaitingConnector:
      "Saved. This control plane cannot yet reconfigure a remote private runtime — apply it on the organization's server, or wait for the private connector.",
    remoteUnsupported:
      "Remote apply is not available in this version. Configuration written here is applied only when Nova runs on the organization's own server.",

    save: "Save configuration",
    saving: "Saving…",
    saved: "Configuration saved.",
    unsaved: "Unsaved changes.",
    discard: "Discard",
    conflict:
      "Someone changed this configuration while you were editing. Reload to see the current state.",
    reload: "Reload",
    readOnly: "Your role can read this page but not change the runtime.",
    hardwareIncompatible:
      "This server's GPU memory is too small for the Advanced profile. Choose Standard or Efficient.",
  },

  configuration: {
    title: "Configuration",
    lead: "Technical settings for this organization.",
    discoveryTitle: "Organization discovery",
    discoveryBody:
      "Lets a workstation find this organization from its identifier, instead of being given an address.",
    discoveryEnabled: "Discovery enabled",
    endpointInvalid: "The announced service endpoint is not acceptable:",
    // Cette page agit sur la decouverte, jamais sur un fournisseur : emprunter
    // le libelle de la page Identity ferait croire le contraire a qui clique.
    disableDiscovery: "Disable discovery",
    enableDiscovery: "Enable discovery",
    save: "Save",
  },

  // Libelles produit, pas noms de champs. Un administrateur decide si ses
  // membres peuvent utiliser une fonction ; il n'a pas a connaitre la cle qui
  // la porte.
  policies: {
    title: "Product policies",
    lead: "What members of this organization can use in Nova.",
    boundary:
      "These settings govern product features only. They never affect sign-in, administrator access, or the audit log.",
    defaults: "Using default settings — nothing has been changed yet.",
    revision: "Revision",
    lastUpdated: "Last updated",
    never: "Never",
    readOnly: "You can view these settings, but not change them.",
    unsaved: "You have unsaved changes.",
    save: "Save changes",
    saving: "Saving…",
    discard: "Discard",
    saved: "Saved.",
    reload: "Reload",
    conflict:
      "The organization policy changed while you were editing it. Reload the latest configuration before saving.",
    enabled: "Enabled",
    disabled: "Disabled",
    // Regroupement par usage plutot que par nom technique : un administrateur
    // cherche « ce qui touche a l'ecriture », pas une cle de configuration.
    groupAi: "AI",
    groupWriting: "Writing",
    groupInput: "Input",
    groupCategories: "App categories",
    stylesTitle: "Styles",
    // La question qu'un administrateur se pose en fermant Styles : les dictees
    // perdent-elles leur mise en forme ? Non.
    stylesBody:
      "Show the Styles category and deliver this organization's Styles. When closed, dictations are still reformulated with the default Style.",
    generatedInstructionsTitle: "Prompts",
    generatedInstructionsBody: "Show the Prompts category, where members find the prompts their dictations produced.",
    pastDictationsTitle: "History",
    pastDictationsBody: "Show the History category, where members find their past dictations.",
    // Prompts et Historique vivent sur le poste : le dire evite de croire que
    // fermer la categorie efface ou collecte quoi que ce soit.
    deviceCategoryNote:
      "Closing it hides the category. Its content stays on each member's device and is never sent to the organization.",
    aiSkillsTitle: "AI Skills",
    aiSkillsBody: "Allow members to use AI Skills in Nova.",
    vocabularyTitle: "Organization vocabulary",
    vocabularyBody:
      "Allow members to use the vocabulary and dictionary provided by this organization.",
    commandsTitle: "Voice commands",
    commandsBody: "Allow members to trigger voice commands in Nova.",
    notesTitle: "Engineering notes",
    notesBody: "Allow members to use engineering notes.",
    fileImportTitle: "Audio file import",
    // La phrase sur la dictee vit dans `dictationSafe`, ajoutee a l'affichage.
    // La repeter ici la faisait apparaitre deux fois de suite a l'ecran.
    fileImportBody: "Allow members to transcribe an existing audio file.",
    // Dit a l'ecran, parce que c'est la question qu'un administrateur se pose
    // en voyant un interrupteur d'import audio.
    dictationSafe: "Microphone dictation always stays available.",
    modeRestricted:
      "Some features may still be unavailable because of this deployment's education mode.",
  },

  groupPolicies: {
    title: "Settings by group",
    lead: "A group can change some settings for its members only. Everything else follows the organization policies above.",
    // La question qu'un administrateur se pose des qu'un membre est dans deux
    // groupes : lequel gagne ? L'ecran le dit.
    restrictive:
      "When a member belongs to several groups that disagree, the most restrictive value applies: disabled rather than enabled, and the shortest dictation limit.",
    empty: "This organization has no groups yet.",
    followsOrganization: "Follows the organization",
    overrideCount: (count: number) =>
      count === 1 ? "1 setting changed for this group" : `${count} settings changed for this group`,
    edit: "Edit",
    view: "View",
    close: "Close",
    inherit: "Follow the organization",
    on: "Enabled for this group",
    off: "Disabled for this group",
    limits: "Limits",
    dictationTitle: "Maximum dictation length (minutes)",
    dictationBody: "Leave empty to follow the organization limit.",
    save: "Save group settings",
    saving: "Saving…",
    cancel: "Cancel",
    unsaved: "You have unsaved changes.",
    readOnly: "You can view these settings, but not change them.",
    conflict: "This group's settings changed while you were editing them. Reload before saving.",
    reload: "Reload",
    notFound: "This group no longer exists.",
  },

  // Libelles produit. Un administrateur choisit du contenu a distribuer ; il
  // n'a pas a connaitre les noms de types du serveur.
  packages: {
    title: "Packages",
    lead: "Content this organization distributes to its members.",
    policyBoundary:
      "Publishing content does not grant permission. A published package only reaches members if the matching product policy is enabled in Configuration.",
    empty: "This organization does not distribute any content yet.",
    readOnly: "You can view this content, but not change it.",
    createTitle: "New package",
    create: "Create package",
    type: "Type",
    name: "Name",
    description: "Description",
    typeStyle: "Style",
    typeAiSkill: "AI Skill",
    typeVocabulary: "Vocabulary",
    typeLabel: (value: string) =>
      value === "style"
        ? "Style"
        : value === "ai_skill"
          ? "AI Skill"
          : value === "vocabulary"
            ? "Vocabulary"
            : value,
    status: "Status",
    // Un libelle, pas seulement une couleur : un etat ne doit pas se deviner a
    // la teinte.
    statusLabel: (value: string) =>
      value === "draft"
        ? "Draft"
        : value === "published"
          ? "Published"
          : value === "archived"
            ? "Archived"
            : value,
    active: "Active",
    incomplete: "Incomplete",
    completeFirst: "Fill in the required fields before publishing.",

    activeVersion: "Active version",
    versions: "Versions",
    noVersion: "No version yet. Create a draft to add content.",
    open: "Open",
    close: "Close",
    newDraft: "Create draft",
    editDraft: "Edit draft",
    editing: "Editing",
    deleteDraft: "Delete draft",
    archive: "Archive",
    archiveTitle: "Archive this package?",
    archiveBody:
      "Members stop receiving this content. Nothing is deleted — every version is kept, and you can restore the package later.",
    restore: "Restore",
    archivedNote:
      "This package is archived. Members no longer receive it. Restore it to work on it again.",
    save: "Save draft",
    discard: "Discard",
    unsaved: "You have unsaved changes.",
    publish: "Publish",
    publishTitle: "Publish this version?",
    publishBody: (version: number) =>
      `Version ${version} becomes the active version and is distributed to members of this organization. Publishing and activating are two states: an earlier version stays published but inactive, and can be made active again to roll back. Published versions can never be edited — to change the content, create a new draft.`,
    activate: "Make active",
    activateTitle: "Change the active version?",
    activateBody: (version: number) =>
      `Members will receive version ${version}. Nothing is rewritten: the version becomes active again exactly as it was published.`,
    styleName: "Style name",
    styleInstruction: "Instruction",
    skillTitle: "Skill title",
    skillSummary: "Summary",
    skillInstruction: "Instruction (required)",
    skillInstructionHint:
      "What the model should do with the text. This is what runs.",
    skillPractice: "Usage tip",
    skillPracticeHint: "Shown to members. Not sent to the model.",
    skillDuration: "Duration (minutes)",
    skillSteps: "Steps",
    addStep: "Add step",
    vocabularyEntries: "Entries",
    vocabularyTerm: "Term",
    vocabularyReplacement: "Replacement",
    addEntry: "Add entry",
    removeRow: "Remove",
  },

  learn: {
    title: "Learn",
    lead: "Choose which lessons members see, which are required and by when, and write lessons of your own.",
    readOnly: "You can view Learn settings, but not change them.",
    newLesson: "New lesson",
    newTitle: "New lesson",
    editTitle: "Edit lesson",
    close: "Close",
    emptyModule: "No lesson in this module.",
    custom: "Written by your organization",
    archived: "Archived",
    required: "Required",
    dueOn: (date: string) => `Due ${date}`,
    groupsAll: "Everyone",
    groupsSome: (count: number) => (count === 1 ? "1 group" : `${count} groups`),
    settings: "Settings",
    requiredLabel: "Required lesson",
    dueDate: "Due date",
    groups: "Groups",
    groupsHint: "No group selected means every member.",
    saveSettings: "Save settings",
    save: "Save lesson",
    saving: "Saving…",
    cancel: "Cancel",
    edit: "Edit",
    archive: "Archive",
    archiveTitle: "Archive this lesson?",
    archiveBody:
      "Members stop seeing this lesson. Nothing is deleted: their progress is kept, and restoring the lesson brings it back as it was.",
    restore: "Restore",
    delete: "Delete lesson",
    deleteTitle: "Delete this lesson permanently?",
    deleteBody:
      "The lesson, its settings and every member's progress on it are removed. This cannot be undone. To hide it without losing anything, archive it instead.",
    module: "Module",
    sourceLanguage: "Source language",
    minutes: "Duration (minutes)",
    minutesShort: (minutes: number) => `${minutes} min`,
    difficulty: "Difficulty",
    difficultyLabel: (value: string) =>
      value === "beginner"
        ? "Beginner"
        : value === "intermediate"
          ? "Intermediate"
          : value === "advanced"
            ? "Advanced"
            : value,
    languages: "Languages",
    addLanguage: "Add a language…",
    removeLanguage: "Remove this language",
    sourceBadge: "source",
    translationNote:
      "Blocks are added, moved and removed in the source language. Here, only their text is translated.",
    lessonTitle: "Title",
    lessonDescription: "Description",
    blocks: "Blocks",
    addBlock: "Add block",
    blockType: "Block type",
    blockLabel: (type: string) =>
      (
        ({
          text: "Text",
          example: "Example",
          comparison: "Before and after",
          tip: "Tip",
          warning: "Warning",
          multiple_choice: "Quiz",
          question: "Quiz",
          reflection: "Reflection",
          prompt_exercise: "Writing exercise",
          takeaway: "Key takeaway",
        }) as Record<string, string>
      )[type] ?? type,
    moveUp: "Move up",
    moveDown: "Move down",
    removeBlock: "Remove",
    dragHint: "Drag a block to reorder it, or use the arrows.",
    fieldLabel: (field: string) =>
      (
        ({
          heading: "Heading (optional)",
          body: "Text",
          worse_label: "Label of the weaker version (optional)",
          worse: "Weaker version",
          better_label: "Label of the stronger version (optional)",
          better: "Stronger version",
          note: "Note (optional)",
          question: "Question",
          explanation: "Explanation shown after answering",
          prompt: "Question to reflect on",
          instruction: "Instruction",
          starting_text: "Starting text (optional)",
          hint: "Hint (optional)",
          sample_answer: "Sample answer",
        }) as Record<string, string>
      )[field] ?? field,
    options: "Answers",
    addOption: "Add an answer",
    removeOption: "Remove",
    correct: "Correct answer",
    correctHint: "Select the correct answer. Members see the explanation after answering.",
    preview: "Preview",
    previewNote: "As members read it in this language. The correct answer is marked here only.",
    untitled: "Untitled lesson",
    worseDefault: "Before",
    betterDefault: "After",
    problemsTitle: "Before saving",
    lessonWide: "Lesson",
    problemLabel: (field: string) =>
      (
        ({
          title: "the title is missing or longer than 120 characters",
          description: "the description is missing or longer than 400 characters",
          blocks: "add between 1 and 40 blocks",
          blocks_mismatch: "the blocks differ from the source language",
          estimated_minutes: "the duration must be between 1 and 10 minutes",
          difficulty: "choose a difficulty",
          module_id: "choose a module",
          missing_source: "the source language has no content",
          options: "every answer needs a label",
          correct_option_id: "select the correct answer",
        }) as Record<string, string>
      )[field] ?? "this text is empty",
    conflict: "This lesson changed while you were editing it. Reload before saving.",
    reload: "Reload",
    invalid: "The server rejected this lesson. Check the fields listed and try again.",
    limit: "This organization has reached the number of lessons it can write.",
    notFound: "This lesson no longer exists.",
  },

  security: {
    title: "Security",
    lead: "How administration of this organization is protected.",
    yourRole: "Your role",
    capabilities: "Your capabilities",
    sessionExpiry: "Administration session expires in",
    modernAuth: "Modern administrator authentication",
    modernAuthOn: "In use — identity provider sign-in with re-authentication",
    stepUp: "Re-authentication",
    stepUpDone: "Performed when this session was opened",
    mfaTitle: "Multi-factor authentication",
    mfaBody:
      "Nova does not verify a second factor itself, and does not know which method was used. Your identity provider applies its own policy, including conditional access.",
    auditTitle: "Recent administrative activity",
    auditBody:
      "Every administrative change is recorded on the server. This view is read-only: no interface can alter or erase it.",
    auditEmpty: "No administrative activity recorded yet.",
    auditWhen: "When",
    auditWho: "Who",
    auditWhat: "What",
    auditTarget: "Target",
    auditMore: "Load older activity",
    auditLegacyActor: "legacy admin token",
    // Valeurs d'un changement, lues comme un administrateur les dirait.
    auditEnabled: "Enabled",
    auditDisabled: "Disabled",
    auditNoValue: "—",
    // SQLite ne protege pas un fichier de celui qui le detient : le dire
    // vaut mieux que laisser croire a une immuabilite qui n'existe pas.
    auditIntegrity:
      "Records are append-only through the application. Anyone with access to the server's database file can still alter them.",
  },

  diagnostics: {
    title: "Diagnostics",
    lead: "Health of this organization's service. No user content is shown here, ever.",
    api: "API",
    apiReachable: "Reachable",
    apiUnreachable: "Unreachable",
    discovery: "Discovery",
    discoveryReachable: "Resolves",
    providers: "Providers",
    version: "Version",
    boundary:
      "Nova Admin manages configuration. Dictated audio, transcripts and prompts never pass through it.",
  },
} as const;

/** Message lisible pour un échec. Jamais de trace technique à l'écran. */
export function errorMessage(kind: ApiErrorKind, code?: string | null): string {
  if (code === "PROVIDER_LAST_ACTIVE") return t.identity.lastActive;
  if (code === "ADMIN_LAST_IDENTITY_MANAGER") return t.administrators.lastManager;
  if (code === "USER_NOT_FOUND") return "This account no longer exists.";
  if (code === "MEMBER_STATUS_INVALID") return "That status is not recognised.";
  if (code === "MEMBER_ROLE_INVALID") return "That member type is not recognised.";
  if (code === "SECURITY_ROLE_INVALID") return "That role is not recognised.";
  if (code === "POLICY_REVISION_CONFLICT") return t.policies.conflict;
  if (code === "GROUP_POLICY_REVISION_CONFLICT") return t.groupPolicies.conflict;
  if (code === "GROUP_NOT_FOUND") return t.groupPolicies.notFound;
  if (code === "LEARNING_REVISION_CONFLICT") return t.learn.conflict;
  if (code === "LEARNING_CUSTOM_LESSON_INVALID") return t.learn.invalid;
  if (code === "LEARNING_CUSTOM_LESSON_LIMIT") return t.learn.limit;
  if (code === "LESSON_NOT_FOUND") return t.learn.notFound;
  if (code === "AI_RUNTIME_REVISION_CONFLICT") return t.aiRuntime.conflict;
  if (code === "AI_RUNTIME_HARDWARE_INCOMPATIBLE")
    return t.aiRuntime.hardwareIncompatible;
  if (code?.startsWith("AI_RUNTIME_CONCURRENCY"))
    return "These concurrency values are out of range. Reload and try again.";
  if (code === "AI_RUNTIME_PROFILE_INVALID")
    return "That model profile is not recognised.";
  // Le motif porte la cle fautive — `POLICY_KEY_UNKNOWN:ai_skils`. La console
  // ne l'affiche pas telle quelle : un nom de champ interne n'aide personne.
  if (code?.startsWith("POLICY_KEY_UNKNOWN"))
    return "This console sent a setting the Nova service does not recognise. Update Nova Admin.";
  if (code?.startsWith("POLICY_VALUE_INVALID"))
    return "One of these settings has an unexpected value. Reload and try again.";
  switch (kind) {
    case "network":
      return "Cannot reach the Nova service. Check your connection and try again.";
    case "unauthenticated":
      return "Your session has ended. Sign in again.";
    case "step_up_required":
      return "Administering requires a recent sign-in.";
    case "admin_session_expired":
      return "Your administration session has ended. Re-authenticate to continue.";
    case "forbidden":
      return "Your account does not have permission for this.";
    case "not_found":
      return "This organization is not available.";
    case "conflict":
      return "This change conflicts with the current configuration.";
    case "invalid":
      return "The server rejected these values. Check the fields and try again.";
    case "contract_mismatch":
      return "This console is older than the Nova service it is talking to. Update Nova Admin.";
    default:
      return "The Nova service could not complete this request.";
  }
}
