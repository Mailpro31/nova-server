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
    administrators: "Administrators",
    members: "Members",
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
    roleMember: "No admin access",
    roleOrganizationAdminHint: "Manages people: administrators, providers, discovery, security.",
    roleItAdminHint: "Manages the service: providers, discovery, deployment.",
    roleReadOnlyHint: "Reads the console. Changes nothing.",
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
      "Nova does not yet report on deployed workstations. This page will show them when it does.",
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
    aiSkillsTitle: "AI Skills",
    aiSkillsBody: "Allow members to use AI Skills in Nova.",
    enabled: "Enabled",
    disabled: "Disabled",
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
