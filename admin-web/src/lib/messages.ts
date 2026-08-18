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
    auditTitle: "Administration audit",
    auditBody:
      "Administrative actions are recorded on the server. No interface exposes them yet.",
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
