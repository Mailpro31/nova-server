/**
 * Textes de la console en français (n° 28).
 *
 * Les écoles françaises lisent la console : le DPO, la DSI, la mission
 * handicap. Mêmes clés que l'anglais (`messages.ts`), vérifiées par le type.
 */

import type { Messages } from "./messages";

export const fr: Messages = {
  product: "Nova Admin",

  nav: {
    overview: "Vue d’ensemble",
    identity: "Identité",
    provisioning: "Provisionnement",
    administrators: "Administrateurs",
    members: "Membres",
    groups: "Groupes",
    learn: "Learn",
    packages: "Paquets",
    limits: "Limites",
    deployment: "Déploiement",
    configuration: "Configuration",
    security: "Sécurité",
    audit: "Journal d’administration",
    diagnostics: "Diagnostic",
  },

  navGroups: {
    overview: "Vue d’ensemble",
    people: "Personnes",
    features: "Fonctions de l’application",
    content: "Contenu",
    limits: "Limites",
    signIn: "Connexion et sécurité",
    audit: "Journal d’administration",
    infrastructure: "Déploiement",
  },

  bootstrap: {
    title: "Nova Admin",
    prompt: "Saisissez l’identifiant de votre organisation",
    hint: "Votre service informatique vous le donne. Ce n’est pas un mot de passe.",
    field: "Organisation",
    submit: "Continuer",
    looking: "Recherche de l’organisation…",
  },

  signIn: {
    title: "Connexion à l’administration",
    lead: "Nova Admin passe par le fournisseur d’identité de votre organisation. Il n’y a pas de mot de passe Nova.",
    microsoft: "Continuer avec Microsoft",
    google: "Continuer avec Google",
    oidc: "Continuer avec l’authentification unique de l’organisation",
    none: "Aucune méthode de connexion n’est configurée pour cette organisation.",
  },

  stepUp: {
    title: "Authentifiez-vous de nouveau pour continuer",
    lead: "Administrer une organisation demande une connexion récente. Votre fournisseur d’identité va vous demander de vous authentifier de nouveau.",
    note: "Nova n’ajoute pas de second facteur à lui. C’est votre fournisseur qui décide de ce qu’il demande.",
    action: "Continuer",
    working: "En attente de votre fournisseur d’identité…",
  },

  denied: {
    title: "Accès refusé",
    lead: "Ce compte ne peut pas administrer cette organisation.",
    hint: "Si c’est inattendu, contactez la personne qui gère Nova dans votre organisation.",
  },

  session: {
    role: "Rôle",
    expiresIn: "La session expire dans",
    signOut: "Se déconnecter",
    signOutAll: "Se déconnecter partout",
  },

  state: {
    loading: "Chargement…",
    empty: "Rien à afficher pour l’instant.",
    retry: "Réessayer",
    notConfigured: "Pas encore configuré",
    unavailable: "Indisponible",
  },

  overview: {
    title: "Vue d’ensemble",
    organization: "Organisation",
    status: "État",
    serverMode: "Mode du serveur",
    deploymentMode: "Mode de déploiement",
    discovery: "Découverte de l’organisation",
    providers: "Fournisseurs de connexion",
    seats: "Places utilisées",
    yourAccess: "Votre accès",
    enabledOf: (enabled: number, total: number) => `${enabled} activés sur ${total}`,
  },

  identity: {
    title: "Identité",
    lead: "Les fournisseurs d’identité qui permettent de se connecter à cette organisation.",
    type: "Type",
    status: "État",
    enabled: "Activé",
    disabled: "Désactivé",
    secret: "Secret client",
    secretConfigured: "Configuré",
    secretAbsent: "Non renseigné",
    replaceSecret: "Remplacer le secret",
    newSecret: "Nouveau secret client",
    save: "Enregistrer",
    disable: "Désactiver le fournisseur",
    disableTitle: "Désactiver ce fournisseur ?",
    disableBody:
      "Les personnes qui se connectent par ce fournisseur ne pourront plus le faire. Les sessions ouvertes ne changent pas.",
    disableConfirm: "Désactiver",
    cancel: "Annuler",
    broken: "Configuration incomplète",
    lastActive:
      "C’est le seul fournisseur activé. Le désactiver bloquerait tous les administrateurs : c’est refusé. Ajoutez d’abord un autre fournisseur.",
    enable: "Activer le fournisseur",
    edit: "Modifier",
    addProvider: "Ajouter un fournisseur",
    addProviderLead:
      "Reliez un fournisseur d’identité pour la connexion des personnes. Il est activé dès son ajout.",
    providerType: "Fournisseur",
    typeLabel: (type: string) =>
      type === "microsoft_entra"
        ? "Microsoft Entra ID"
        : type === "google_workspace"
          ? "Google Workspace"
          : type === "oidc"
            ? "Autre fournisseur OpenID Connect"
            : type,
    displayName: "Nom affiché (facultatif)",
    tenantId: "Identifiant du locataire Microsoft Entra",
    hostedDomain: "Domaine Google Workspace",
    issuer: "Adresse de l’émetteur (https)",
    clientId: "Identifiant client",
    clientSecret: "Secret client",
    authMethod: "Authentification du jeton",
    authMethodLabel: (method: string) =>
      method === "client_secret_post"
        ? "Secret client dans le corps de la requête"
        : method === "client_secret_basic"
          ? "Secret client dans l’en-tête d’autorisation"
          : method === "none"
            ? "Pas de secret client"
            : method,
    requiredClaim: "Revendication exigée (facultatif)",
    requiredClaimValue: "Valeur exigée (facultatif)",
    secretKeptHint: "Laissez le secret client vide pour garder l’actuel.",
    create: "Ajouter le fournisseur",
    creating: "Ajout…",
    problemsTitle: "Avant l’ajout",
    problemLabel: (field: string) =>
      (
        ({
          entra_tenant_id: "saisissez l’identifiant du locataire Microsoft Entra",
          google_hosted_domain: "saisissez le domaine Google Workspace",
          oidc_issuer: "saisissez une adresse d’émetteur commençant par https://",
          oidc_client_id: "saisissez l’identifiant client",
          oidc_client_secret: "saisissez le secret client",
          provider_type: "choisissez un fournisseur",
        }) as Record<string, string>
      )[field] ?? field,
  },

  provisioning: {
    title: "Identité / Provisionnement",
    lead: "Automatiser les membres de l’organisation par une connexion SCIM 2.0 propre à l’organisation.",
    endpoint: "Adresse du locataire",
    status: "État SCIM",
    active: "Actif",
    notConfigured: "Non configuré",
    managedUsers: "Utilisateurs gérés par SCIM",
    managedGroups: "Groupes gérés par SCIM",
    lastRequest: "Dernière requête",
    lastSuccess: "Dernier provisionnement réussi",
    lastError: "Dernière erreur",
    never: "Jamais",
    credentials: "Identifiants",
    credentialName: "Nom de l’identifiant",
    providerContext: "Contexte de rapprochement",
    genericProvider: "Client SCIM générique",
    entraProvider: "Microsoft Entra ID",
    googleProvider: "Google Workspace",
    oidcProvider: "Fournisseur OIDC générique",
    generate: "Créer un identifiant",
    generating: "Création…",
    generatedTitle: "Jeton secret — affiché une seule fois",
    generatedBody:
      "Copiez ce jeton maintenant. Nova n’en garde que l’empreinte et ne peut plus l’afficher.",
    copied: "Copié.",
    copy: "Copier le jeton",
    done: "Je l’ai enregistré",
    created: "Créé",
    lastUsed: "Dernière utilisation",
    revoked: "Révoqué",
    expires: "Expire",
    revoke: "Révoquer",
    revokeTitle: "Révoquer cet identifiant SCIM ?",
    revokeBody:
      "Le client de provisionnement qui l’utilise est refusé aussitôt. Les autres identifiants actifs continuent de fonctionner.",
    revokeConfirm: "Révoquer l’identifiant",
    readOnly: "Votre rôle permet de consulter le provisionnement, mais pas de créer ni de révoquer des identifiants.",
    groupsTitle: "Groupes gérés par SCIM",
    group: "Groupe",
    members: "Membres",
    updated: "Mis à jour",
    noGroups: "Aucun groupe n’a encore été provisionné.",
    entraTitle: "Configurer le provisionnement Microsoft Entra",
    entraSteps: [
      "Dans Microsoft Entra, créez ou ouvrez l’application d’entreprise de Nova.",
      "Sous Provisionnement, choisissez Automatique et collez l’adresse du locataire et le jeton secret de cette page.",
      "Testez la connexion, puis sous Utilisateurs et groupes, attribuez les groupes dont les membres utilisent Nova (par exemple chaque classe ou chaque service).",
      "Limitez l’étendue aux utilisateurs et groupes attribués, et lancez le provisionnement. Microsoft envoie ensuite seul les comptes, les groupes et leurs changements, environ toutes les 40 minutes.",
    ],
    entraWarning:
      "Ne saisissez ici ni mot de passe Microsoft ni secret client Entra. Le SCIM entrant n’utilise que le jeton secret de Nova.",
    genericTitle: "Tout autre annuaire SCIM",
    genericLead:
      "Okta, JumpCloud, OneLogin, Keycloak et la plupart des annuaires peuvent provisionner Nova en SCIM 2.0, comme Microsoft Entra.",
    genericSteps: [
      "Créez un identifiant sur cette page, avec le contexte générique.",
      "Dans votre annuaire, ajoutez une application SCIM 2.0 et collez l’adresse du locataire et le jeton secret (Bearer).",
      "Activez le provisionnement des utilisateurs et des groupes, désactivation comprise, et attribuez les groupes dont les membres utilisent Nova.",
      "Lancez la synchronisation, puis vérifiez que les comptes et les groupes apparaissent dans Membres et Groupes.",
    ],
    departureNote:
      "Quand l’annuaire désactive ou retire une personne, son compte Nova est suspendu et elle est déconnectée de tous ses appareils. Rien n’est supprimé : la réactiver dans l’annuaire lui rend son accès.",
  },

  administrators: {
    title: "Administrateurs",
    lead: "Qui peut administrer cette organisation, et ce que chacun peut faire.",
    person: "Personne",
    role: "Rôle",
    status: "État",
    none: "Aucun administrateur pour l’instant.",
    bootstrapNote:
      "Le premier administrateur est désigné sur le serveur, en ligne de commande. Nova Admin gère les suivants.",
    roleOrganizationAdmin: "Administrateur de l’organisation",
    roleItAdmin: "Administrateur informatique",
    roleReadOnly: "Lecture seule",
    roleContentEditor: "Éditeur de contenu",
    roleMember: "Sans accès d’administration",
    roleOrganizationAdminHint: "Gère les personnes : administrateurs, fournisseurs, découverte, sécurité.",
    roleItAdminHint: "Gère le service : fournisseurs, découverte, déploiement.",
    roleReadOnlyHint: "Consulte la console. Ne modifie rien.",
    roleContentEditorHint:
      "Gère le contenu : leçons Learn, Styles, Prompts et vocabulaire. Pas d’accès aux membres, à la sécurité ni aux règles.",
    roleMemberHint: "Utilise Nova, n’administre rien.",
    changeRole: "Changer le rôle",
    removeAccess: "Retirer l’accès d’administration",
    removeTitle: "Retirer l’accès d’administration ?",
    removeBody:
      "La personne garde son compte Nova et peut toujours dicter. Elle n’administre plus, et ses sessions d’administration se ferment aussitôt.",
    removeConfirm: "Retirer l’accès",
    changeTitle: "Changer le rôle de cet administrateur ?",
    changeBody:
      "Ses sessions d’administration ouvertes se ferment aussitôt. Elle s’authentifiera de nouveau pour continuer, avec ses nouveaux droits.",
    changeConfirm: "Changer le rôle",
    lastManager:
      "Il doit rester au moins un administrateur de l’organisation. Donnez d’abord ce rôle à quelqu’un d’autre.",
    addTitle: "Donner un accès d’administration",
    addLead: "Cherchez une personne qui utilise déjà Nova dans cette organisation.",
    searchLabel: "E-mail",
    searchPlaceholder: "Commencez à saisir une adresse e-mail",
    searchShort: "Saisissez au moins deux caractères.",
    searchEmpty: "Aucun compte ne correspond.",
    grant: "Donner l’accès",
    grantedTo: (email: string) => `${email} est maintenant administrateur.`,
  },

  members: {
    title: "Membres",
    lead: "Qui appartient à cette organisation, et quels accès chacun a.",
    boundary:
      "Identité et accès seulement. Nova Admin ne montre jamais ce que quelqu’un a dicté, écrit ou demandé.",
    search: "Rechercher par e-mail",
    searchPlaceholder: "Commencez à saisir une adresse e-mail",
    filterAll: "Tous",
    filterActive: "Actifs",
    filterDisabled: "Suspendus",
    none: "Aucun membre ne correspond.",
    count: (shown: number, total: number) => `${shown} membres sur ${total}`,
    more: "Affinez la recherche pour voir les autres.",
    memberType: "Type",
    groups: "Groupes",
    noGroup: "Aucun",
    devices: "Appareils",
    noDevice: "Aucun appareil connecté.",
    identities: "Méthodes de connexion",
    adminSessions: "Sessions d’administration ouvertes",
    note: "Note d’administration",
    open: "Ouvrir",
    back: "Retour aux membres",
    suspend: "Suspendre le compte",
    reactivate: "Réactiver le compte",
    suspendTitle: "Suspendre ce compte ?",
    suspendBody:
      "La personne est déconnectée de tous ses appareils aussitôt et ne peut plus se connecter. Son compte, son identité et son historique sont gardés : ce n’est pas une suppression.",
    suspendConfirm: "Suspendre",
    reactivateTitle: "Réactiver ce compte ?",
    reactivateBody:
      "La personne pourra se connecter de nouveau. Les anciennes sessions ne reviennent pas : elle se reconnecte.",
    reactivateConfirm: "Réactiver",
    revokeSessions: "Déconnecter de tous les appareils",
    revokeTitle: "Déconnecter cette personne partout ?",
    revokeBody:
      "Tous les appareils sont déconnectés. Le compte reste actif et la personne peut se reconnecter tout de suite.",
    revokeConfirm: "Déconnecter partout",
    revoked: (devices: number) =>
      devices === 1 ? "Déconnecté de 1 appareil." : `Déconnecté de ${devices} appareils.`,
    remove: "Supprimer le compte",
    removeTitle: "Supprimer définitivement ce compte ?",
    removeBody:
      "Le compte, ses sessions, son dictionnaire personnel et ses méthodes de connexion sont effacés. Le journal d’administration est gardé : il dit qui a fait quoi, pas ce que quelqu’un a dicté. Suspendre est en général le meilleur choix.",
    removeConfirm: "Supprimer définitivement",
    lastManager:
      "Il doit rester au moins un administrateur de l’organisation. Ce compte ne peut être ni suspendu ni supprimé.",
    saveProfile: "Enregistrer",
    profileSaved: "Enregistré.",
    groupDisplayTitle: "Groupes affichés dans Nova Desktop",
    groupDisplayHint:
      "Le choix fait pour cette personne l’emporte sur ses groupes et sur les réglages de l’organisation. Un groupe masqué aux membres reste masqué.",
    groupDisplayInherit: "Suivre les groupes et l’organisation",
    groupDisplayShow: "Afficher ses groupes",
    groupDisplayHide: "Masquer ses groupes",
    groupDisplayEffective: (visible: boolean) =>
      visible
        ? "Cette personne voit actuellement ses groupes."
        : "Cette personne ne voit pas ses groupes actuellement.",
    addMember: "Ajouter un membre",
    addMemberLead:
      "Préparez un compte avant la première connexion de la personne : il est repris quand elle se connecte avec cette adresse. Ajouter un membre ne donne aucun accès d’administration.",
    email: "E-mail",
    add: "Ajouter le membre",
    adding: "Ajout…",
    outsideDomains: "Cette adresse n’est pas dans les domaines de messagerie de l’organisation.",
    emailInvalid: "Saisissez une adresse e-mail valide.",
    alreadyExists: "Un compte avec cette adresse existe déjà.",
  },

  groups: {
    title: "Groupes",
    lead: "Les groupes servent à cibler des leçons et à changer des réglages pour une partie de l’organisation. Créez les vôtres ici ; ceux de votre annuaire apparaissent aussi.",
    create: "Créer le groupe",
    creating: "Création…",
    name: "Nom du groupe",
    source: "Origine",
    sourceConsole: "Créé dans Nova Admin",
    sourceDirectory: "Venu de votre annuaire",
    members: "Membres",
    memberCount: (count: number) => (count === 1 ? "1 membre" : `${count} membres`),
    open: "Membres",
    close: "Fermer",
    rename: "Renommer",
    save: "Enregistrer",
    cancel: "Annuler",
    delete: "Supprimer le groupe",
    deleteTitle: "Supprimer ce groupe ?",
    deleteBody:
      "Le groupe et ses appartenances sont retirés. Les réglages et les leçons qui le ciblaient ne s’appliquent plus à ses membres. C’est irréversible.",
    managedByDirectory: "Ce groupe vient de votre annuaire. Modifiez-le là-bas.",
    empty: "Aucun groupe pour l’instant.",
    noMembers: "Aucun membre dans ce groupe pour l’instant.",
    addMember: "Ajouter un membre",
    searchPlaceholder: "Rechercher par e-mail",
    noMatch: "Aucun autre membre ne correspond.",
    add: "Ajouter",
    remove: "Retirer",
    readOnly: "Vous pouvez consulter les groupes, mais pas les modifier.",
    nameTaken: "Un groupe porte déjà ce nom.",
    nameInvalid: "Saisissez un nom de groupe de 120 caractères au plus.",
    display: "Dans Nova Desktop",
    visibleToMembers: "Affiché à ses membres",
    hiddenFromMembers: "Masqué à ses membres",
    hideFromMembers: "Masquer aux membres",
    showToMembers: "Afficher aux membres",
    visibilityHint:
      "Un groupe masqué applique toujours ses leçons et ses réglages ; seul son nom n’est plus envoyé aux membres.",
  },

  import: {
    title: "Importer une liste de membres",
    lead: "Importez un tableau de membres, avec leur groupe. Rien n’est écrit avant que vous ayez confirmé ce que le fichier ferait.",
    columns:
      "Colonnes attendues : email (obligatoire), group (facultatif, plusieurs séparés par ;) et type (facultatif : student, teacher, staff, employee, manager, partner).",
    formats: "CSV ou XLSX, 5 000 lignes au plus.",
    chooseFile: "Choisir un fichier",
    mode: "Mode",
    modeAdd: "Ajouter seulement : personne n’est jamais retiré d’un groupe.",
    modeSync:
      "Refléter le fichier : dans les groupes qu’il cite, les membres absents du fichier sont retirés du groupe.",
    nothingDeleted: "Aucun compte n’est jamais supprimé, dans aucun mode.",
    preview: "Aperçu",
    apply: "Importer",
    previewing: "Lecture…",
    applying: "Import…",
    previewResult: (rows: number, accounts: number, added: number, removed: number) =>
      `${rows} lignes lues : ${accounts} comptes à créer, ${added} appartenances à ajouter, ${removed} à retirer.`,
    appliedResult: (accounts: number, added: number, removed: number) =>
      `Terminé : ${accounts} comptes créés, ${added} appartenances ajoutées, ${removed} retirées.`,
    groupsToCreate: (groups: string[]) => `Groupes à créer : ${groups.join(", ")}.`,
    refused: "Lignes refusées",
    invalidLine: (line: number, reason: string) => `Ligne ${line} : ${reason}`,
    emailColumnMissing: "Aucune colonne d’e-mail trouvée. Nommez une colonne email ou adresse.",
    fileUnsupported: "Seuls les fichiers CSV et XLSX s’importent.",
    tooManyRows: "Ce fichier dépasse 5 000 lignes. Découpez-le et importez chaque partie.",
    fileTooLarge: "Ce fichier est trop volumineux pour être importé.",
    modeInvalid: "Choisissez « ajouter » ou « refléter ».",
  },

  googleSync: {
    title: "Annuaire Google Workspace",
    lead: "Nova lit vos groupes Google et leurs membres, et tient ses propres groupes à jour. En lecture seule : Nova n’écrit jamais dans votre annuaire.",
    steps: [
      "Dans Google Cloud, créez un compte de service pour Nova et générez une clé JSON.",
      "Dans la console d’administration Google, sous Sécurité puis Commandes des API, ajoutez la délégation au niveau du domaine pour l’identifiant client de ce compte de service.",
      "N’accordez que les deux portées en lecture seule : admin.directory.group.readonly et admin.directory.group.member.readonly.",
      "Placez la clé JSON sur le serveur dans une variable d’environnement ou un fichier monté, puis collez sa référence ci-dessous avec l’adresse de l’administrateur au nom duquel Nova agit.",
    ],
    enabled: "Synchroniser cet annuaire",
    adminSubject: "Administrateur au nom duquel agir",
    adminSubjectHint:
      "Un administrateur Google de votre domaine. Google n’accorde l’accès à l’annuaire qu’au nom d’une personne.",
    customerId: "Identifiant client Google",
    keyReference: "Clé du compte de service",
    keyHint:
      "Une référence seulement, comme env://NOVA_GOOGLE_SERVICE_ACCOUNT_KEY ou file:///run/secrets/google-key.json. La clé elle-même n’est jamais envoyée à Nova ni stockée dans la base.",
    groupFilter: "Groupes à synchroniser",
    groupFilterHint:
      "Une adresse de groupe par ligne, par exemple aero2@example.com. Laissez vide pour synchroniser tous les groupes.",
    save: "Enregistrer",
    saved: "Enregistré.",
    runNow: "Synchroniser maintenant",
    running: "Synchronisation…",
    hourly: "Nova synchronise aussi de lui-même, environ une fois par heure.",
    never: "Jamais synchronisé.",
    lastRun: "Dernière synchronisation",
    statusOk: "Réussie",
    statusError: "Échouée",
    outcome: (groups: number, added: number, removed: number) =>
      `${groups} groupe${groups === 1 ? "" : "s"}, ${added} ajoutés, ${removed} retirés.`,
    readOnlyGroups:
      "Les groupes venus de Google sont en lecture seule dans Nova Admin : renommez-les ou supprimez-les dans Google.",
    keyReferenceRequired:
      "Saisissez une référence comme env://NOM ou file:///chemin, pas la clé elle-même.",
    adminSubjectRequired: "Saisissez l’administrateur au nom duquel Nova doit agir.",
    unauthorized:
      "Google a refusé les identifiants. Vérifiez la délégation au niveau du domaine et l’adresse de l’administrateur.",
    forbidden:
      "Google a refusé la demande. Vérifiez que les deux portées en lecture seule de l’annuaire sont accordées.",
    unavailable: "Google n’a pas répondu. Rien n’a été modifié.",
    disabled: "Activez cette synchronisation avant de la lancer.",
  },

  dns: {
    title: "Trouver cette organisation par l’adresse e-mail",
    lead: "Publiez un enregistrement DNS, et vos membres se connectent avec leur seule adresse e-mail, depuis n’importe quel réseau. Ils ne saisissent jamais d’adresse de serveur.",
    howItWorks:
      "Nova prend le domaine de l’adresse, lit cet enregistrement, et ne se fie à ce qu’il trouve que si l’adresse est en HTTPS, que son hôte appartient à ce domaine et que ce serveur confirme le servir.",
    record: "Enregistrement à publier",
    name: "Nom",
    type: "Type",
    value: "Valeur",
    check: "Vérifier",
    checking: "Lecture du DNS…",
    statusMatch: "Publié et pointant ici.",
    statusMissing: "Pas encore publié. Un changement DNS peut mettre quelques heures à apparaître.",
    statusMismatch: "Publié, mais pointant vers une autre adresse :",
    unavailable: "Impossible de lire le DNS. Rien n’a été modifié ; réessayez dans un instant.",
    domainNotServed: "Cette organisation ne sert pas ce domaine de messagerie.",
    endpointMissing:
      "Renseignez d’abord l’adresse du service : sans l’adresse HTTPS de ce serveur, il n’y a pas d’enregistrement à publier.",
    guide:
      "Le guide de déploiement décrit le tunnel sortant, l’adresse HTTPS et les réglages qui vont avec.",
  },

  rules: {
    title: "Règles de tri",
    lead: "Rangez les membres dans des groupes d’après ce que votre annuaire dit d’eux, sans toucher à chaque personne.",
    consoleGroupsOnly:
      "Les règles ne remplissent que les groupes créés dans Nova Admin. Un groupe venu de votre annuaire reste géré par votre annuaire.",
    add: "Ajouter une règle",
    ifField: "Si",
    operator: "correspond à",
    value: "cette valeur",
    thenGroup: "alors ajouter à",
    enabled: "Active",
    disabled: "En pause",
    save: "Enregistrer la règle",
    cancel: "Annuler",
    edit: "Modifier",
    delete: "Supprimer la règle",
    deleteTitle: "Supprimer cette règle ?",
    deleteBody:
      "Les personnes ajoutées par cette règle quittent le groupe. Les membres ajoutés à la main restent. C’est irréversible.",
    preview: "Aperçu",
    previewing: "Décompte…",
    previewResult: (count: number, sample: string[]) =>
      count === 0
        ? "Aucun membre ne correspond encore à cette règle."
        : `${count} membre${count === 1 ? " correspond" : "s correspondent"}, par exemple ${sample.join(", ")}.`,
    applyNow: "Trier tout le monde maintenant",
    applied: (added: number, removed: number) => `${added} ajoutés, ${removed} retirés.`,
    empty: "Aucune règle pour l’instant.",
    memberByRule:
      "Une règle a ajouté ce membre. Modifiez ou supprimez la règle pour le retirer, sinon le prochain tri le remet.",
    fieldInvalid: "Ce champ n’est pas reconnu.",
    operatorInvalid: "Cette comparaison n’est pas reconnue.",
    valueInvalid: "Saisissez une valeur de 200 caractères au plus.",
    limitReached: "Cette organisation a déjà le nombre maximal de règles.",
    notFound: "Cette règle n’existe plus.",
    fieldLabels: {
      department: "Service",
      organization: "Organisation",
      email_domain: "Domaine de l’e-mail",
      member_type: "Type de membre",
    } as Record<string, string>,
    operatorLabels: {
      equals: "est exactement",
      contains: "contient",
      starts_with: "commence par",
    } as Record<string, string>,
  },

  deployment: {
    title: "Déploiement",
    lead: "Où tourne le service Nova de cette organisation.",
    mode: "Mode de déploiement",
    endpoint: "Adresse du service",
    slug: "Identifiant de découverte",
    version: "Version du serveur",
    fleetTitle: "Parc de postes",
    fleetBody:
      "Les postes déployés ne rendent pas compte à Nova. Ce panneau demanderait un canal de remontée qui n’existe pas : aucun état du parc n’est affiché plutôt que deviné.",

    desktopTitle: "Déploiement Windows",
    desktopLead:
      "Préparez un paquet par machine que votre service informatique peut installer en silence sur chaque poste.",
    packageStatus: "État du paquet",
    statusNotConfigured: "Non configuré",
    statusPackageReady: "Paquet prêt",
    statusConfigurationError: "Erreur de configuration",
    installer: "Installateur",
    origin: "Origine du plan de contrôle",
    originNote:
      "Figée dans le paquet. Une règle de déploiement choisit un profil ; elle ne peut pas diriger Nova vers une autre adresse.",
    deviceReporting: "Remontée des postes",
    deviceReportingNone:
      "Aucune. Les postes installés n’envoient aucun état à Nova : cette page décrit le paquet et sa configuration, jamais la santé d’une machine.",
    releaseTitle: "Version disponible",
    releaseNone:
      "Aucun manifeste de version n’est configuré sur ce service : aucune version n’est recommandée. Nova ne nomme pas une version qu’il ne peut pas vérifier.",
    releaseUnsigned:
      "Aucune racine de confiance n’est configurée pour les versions de l’application. Le manifeste est servi tel quel, sans prétendre être signé.",
    releaseError: "Le manifeste de version est configuré mais n’a pas pu être validé.",
    desktopVersion: "Version",
    minimumVersion: "Version minimale prise en charge",
    artifact: "Fichier",
    digest: "SHA-256",
    signed: "Signature",
    signedYes: "Vérifiée",
    signedNo: "Non vérifiée",

    profilesTitle: "Profils de déploiement",
    profileName: "Nom du profil",
    channel: "Canal de version",
    channelStable: "Stable",
    channelPreview: "Aperçu",
    updatePolicy: "Mises à jour",
    policyManual: "Manuelles",
    policyNotify: "Avec notification",
    policyAutomatic: "Automatiques",
    revision: "Révision de la configuration",
    profileStatus: "État",
    profileActive: "Actif",
    profileRetired: "Retiré",
    create: "Créer le profil",
    creating: "Création…",
    retire: "Retirer",
    reactivate: "Réactiver",
    edit: "Modifier",
    save: "Enregistrer le profil",
    cancel: "Annuler",
    retireTitle: "Retirer ce profil de déploiement ?",
    retireBody:
      "Les nouvelles installations qui l’utilisent ne seront plus reconnues. Les postes déjà installés continuent avec la configuration qu’ils ont appliquée.",
    retireConfirm: "Retirer le profil",
    noProfiles:
      "Aucun profil de déploiement pour l’instant. Créez-en un pour obtenir une commande d’installation et un fichier de configuration machine.",
    readOnly:
      "Votre rôle permet de consulter le déploiement, mais pas de le modifier. Le déploiement relève du rôle d’administrateur informatique.",

    installCommand: "Commande d’installation silencieuse",
    installCommandNote:
      "Ne contient aucun secret durable. L’identifiant de déploiement n’authentifie personne : chaque utilisateur se connecte toujours par l’authentification unique.",
    managedConfig: "Configuration machine",
    managedConfigNote:
      "Écrivez-la dans C:\\ProgramData\\Nova\\organization.json, ou laissez l’installateur l’écrire à partir de la commande ci-dessus.",
    copy: "Copier",
    copied: "Copié.",
  },

  connector: {
    title: "Connecteur privé",
    lead: "Comment le déploiement de cette organisation reçoit sa configuration de Nova.",
    privacy:
      "Le connecteur ne synchronise que des métadonnées de contrôle : révisions, état, versions et santé. L’audio dicté, les consignes, les transcriptions et les sorties du modèle restent dans l’infrastructure de l’organisation.",
    outbound:
      "Le connecteur appelle Nova ; Nova n’appelle jamais votre réseau. Aucun port entrant n’est à ouvrir, et Nova ne connaît aucune adresse privée.",

    none: "Aucun connecteur n’est inscrit. Tant qu’il n’y en a pas, la configuration enregistrée ici attend au lieu d’être appliquée.",
    pending: "code(s) d’inscription en attente",

    status: "État",
    statusOnline: "En ligne",
    statusDegraded: "Dégradé",
    statusOffline: "Hors ligne",
    statusRevoked: "Révoqué",
    lastSeen: "Vu pour la dernière fois",
    never: "Jamais",
    deployment: "Déploiement",
    connectorVersion: "Version du connecteur",
    serverVersion: "Version du serveur Nova",
    protocol: "Protocole",
    protocolUnsupported: "Protocole non pris en charge : ce connecteur doit être mis à jour avant de pouvoir appliquer une configuration.",
    updateRecommended: "Un protocole de connecteur plus récent existe. La mise à jour est conseillée ; rien ne casse d’ici là.",
    capabilities: "Capacités",
    runtimeStatus: "Moteur d’IA",
    deliveredRevision: "Révision livrée",
    appliedRevision: "Révision appliquée",
    applyStatus: "Configuration",
    lastError: "Dernière erreur",

    enrollTitle: "Inscrire un connecteur",
    enrollBody:
      "Crée un code à usage unique. Lancez la commande ci-dessous sur le serveur de l’organisation ; le code cesse de fonctionner une fois utilisé, et à son expiration.",
    enrollName: "Nom",
    enrollDeployment: "Identifiant de déploiement",
    enrollAction: "Créer un code d’inscription",
    enrollCreating: "Création…",
    enrollOnce: "Ce code n’est affiché qu’une fois. Nova n’en garde que l’empreinte et ne peut plus l’afficher.",
    enrollExpires: "Expire dans",
    enrollCommand: "À lancer sur le serveur de l’organisation",
    enrollDone: "Terminé",

    versionTitle: "Version et mises à jour",
    runsOn: "Tourne sur",
    distribution: "Distribution",
    selfUpdate: "Mise à jour autonome",
    selfUpdateYes: "Prise en charge",
    selfUpdateNo: "Pilotée par votre déploiement",
    selfUpdateUnknown: "Non indiquée : ce connecteur est antérieur à la prise en charge des mises à jour",
    externalRequired: "Mise à jour par votre déploiement nécessaire",
    externalBody:
      "Une version plus récente existe. Ce connecteur tourne dans un conteneur et ne peut pas remplacer sa propre image : Nova ne le tentera pas. Récupérez la nouvelle image et recréez le service comme vous le déployez ; le connecteur garde son identité, son identifiant et sa configuration appliquée.",
    selfUpdateBody:
      "Ce connecteur tourne dans un conteneur, et un conteneur ne peut pas remplacer sa propre image. Mettez-le à jour comme vous le déployez : récupérez la nouvelle image et recréez le service. Nova ne propose pas un bouton qui ne pourrait pas marcher.",
    installedVersion: "Installée",
    recommendedVersion: "Recommandée",
    minimumVersion: "Minimale prise en charge",
    supportStatus: "Prise en charge",
    supportUpToDate: "À jour",
    supportUpdateAvailable: "Mise à jour disponible",
    supportUnsupported: "Trop ancienne : mise à jour conseillée",
    supportUnknown: "Inconnue",
    unsupportedBody:
      "Ce connecteur est plus ancien que la version minimale prise en charge. Il continue de tourner et de servir votre déploiement : le déconnecter ne ferait que l’empêcher de recevoir la mise à jour qui le remet en règle.",
    updateState: "État de la mise à jour",
    lastUpdate: "Dernière mise à jour",
    lastKnownGood: "Dernière version saine",
    updateError: "Dernière erreur de mise à jour",
    requestUpdate: "Mettre à jour vers",
    requestingUpdate: "Demande…",
    updateBody:
      "Nova envoie une version, jamais une commande. Le connecteur récupère lui-même la version, vérifie sa signature et son empreinte, la prépare, et ne signale la réussite qu’une fois la nouvelle version lancée et saine. Sinon, il revient à la précédente.",
    neverUpdated: "Jamais",

    credentialTitle: "Identifiant",
    credentialAge: "Âge",
    credentialGeneration: "Génération",
    lastRotated: "Dernier renouvellement",
    neverRotated: "Jamais renouvelé",
    rotationState: "Renouvellement",
    rotationStable: "Sain",
    rotationRequested: "Demandé : en attente du connecteur",
    rotationPending: "En cours : en attente de la confirmation du connecteur",
    rotationFailed: "Le dernier renouvellement n’a pas abouti. L’identifiant actuel fonctionne toujours ; vous pouvez en demander un autre.",
    rotationUnsupported:
      "Ce connecteur est trop ancien pour renouveler son identifiant. Mettez-le à jour, ou révoquez-le et inscrivez-le de nouveau.",
    rotationBody:
      "Le renouvellement remplace l’identifiant du connecteur sans nouvelle inscription. Nova ne montre jamais le nouveau secret : le connecteur le reçoit directement et prouve qu’il sait s’en servir avant que l’ancien cesse de fonctionner.",
    requestRotation: "Demander un renouvellement",
    requestingRotation: "Demande…",
    rotationCompromised:
      "Si vous pensez que l’identifiant est compromis, révoquez plutôt le connecteur. Le renouvellement suppose que l’ancien identifiant fonctionne encore.",

    revoke: "Révoquer",
    revoking: "Révocation…",
    revokeBody:
      "La révocation arrête la synchronisation, pas la dictée : le déploiement continue avec la configuration qu’il a déjà appliquée.",
    readOnly: "Votre rôle permet de lire cette page, mais pas d’inscrire ni de révoquer un connecteur.",
  },

  aiRuntime: {
    title: "Moteur d’IA",
    lead: "Où se fait réellement le traitement par IA de cette organisation.",
    privacy:
      "Les dictées, transcriptions, consignes et sorties du modèle restent dans l’infrastructure de l’organisation. Le plan de contrôle de Nova ne stocke que la configuration et la santé, jamais le contenu des personnes.",
    processing: "Lieu du traitement",
    onPremises: "Infrastructure privée de l’organisation",
    externalProvider: "Fournisseur d’IA en ligne (API) choisi dans la configuration du serveur",
    privacyExternal:
      "Les transcriptions et le Style choisi sont envoyés au fournisseur d’IA en ligne configuré sur le serveur, qui les relit et les met au propre. Le plan de contrôle de Nova ne stocke que la configuration et la santé, jamais le contenu des personnes.",
    cloudFallbackExternalBody:
      "Si le fournisseur en ligne échoue trois fois de suite, le modèle local du serveur prend le relais pendant 15 secondes.",
    cloudFallback: "Repli vers le cloud",
    cloudFallbackDisabled: "Désactivé",
    cloudFallbackBody:
      "Si le moteur privé est arrêté, les demandes échouent avec un message clair. Nova n’envoie jamais de contenu en silence à un fournisseur d’IA externe.",

    statusTitle: "État du moteur",
    status: "Moteur",
    statusHealthy: "Sain",
    statusDegraded: "Dégradé",
    statusStarting: "Démarrage",
    statusUnavailable: "Indisponible",
    textEngine: "Moteur de texte",
    speechEngine: "Moteur de la parole",
    engineReady: "Prêt",
    engineLoading: "Chargement",
    engineUnavailable: "Indisponible",
    lastCheck: "Dernier contrôle de santé",
    never: "Jamais",
    adapter: "Adaptateur",
    model: "Modèle chargé",
    runtimeVersion: "Version du moteur",
    latency: "Latence du contrôle",
    lastError: "Dernière erreur",
    capacity: "En cours / en file",
    advanced: "Détails avancés",

    hardwareTitle: "Matériel détecté",
    cpu: "Processeur",
    architecture: "Architecture",
    ram: "Mémoire",
    gpu: "Carte graphique",
    vram: "Mémoire graphique",
    driver: "Pilote",
    noGpu: "Aucune carte graphique prise en charge détectée",
    recommended: "Profil recommandé",
    recommendedBody:
      "D’après le matériel indiqué par ce serveur. Nova ne l’applique jamais de lui-même : le choix du profil reste une décision de l’administrateur.",

    configTitle: "Configuration",
    profile: "Profil de modèle",
    profileEfficient: "Économe : latence et ressources les plus faibles",
    profileStandard: "Standard : équilibré, recommandé",
    profileAdvanced: "Avancé : qualité et raisonnement les plus élevés",
    profileCustom: "Personnalisé : déclaré par ce déploiement",
    customUnavailable:
      "Le profil personnalisé n’est pas proposé par ce déploiement. Il décrit des paramètres déclarés dans la configuration du serveur : il n’est sélectionnable qu’une fois que votre service informatique les a posés.",
    customTitle: "Paramètres du profil personnalisé",
    customBody:
      "Déclarés dans la configuration du déploiement et affichés ici pour le diagnostic. Ils ne se modifient pas depuis cette console.",
    customTokens: "Longueur maximale de sortie",
    customTemperature: "Température",
    customReasoning: "Raisonnement",
    profileBody:
      "Un profil décrit le comportement voulu. Le modèle qui le sert relève de la configuration du moteur, pas de cet écran.",
    reasoning: "Autoriser le raisonnement approfondi",
    reasoningBody:
      "Permet aux tâches longues de réfléchir avant de répondre. La dictée et la mise au propre restent directes dans tous les cas : la latence y compte davantage.",
    concurrency: "Simultanéité",
    concurrencyAuto: "Automatique",
    concurrencyManual: "Contrôlée",
    concurrencyText: "Demandes de texte simultanées",
    concurrencySpeech: "Demandes de parole simultanées",
    concurrencyQueue: "Longueur de la file",
    concurrencyBody:
      "Quand le moteur est saturé, Nova répond « occupé » plutôt que de laisser les demandes attendre jusqu’au délai maximal.",

    stateTitle: "Voulu et appliqué",
    desiredRevision: "Révision voulue",
    appliedRevision: "Révision appliquée",
    applyStatus: "État de l’application",
    pending: "Enregistrée, pas encore appliquée par le moteur",
    appliedLocal: "Appliquée par ce serveur",
    localDefault: "Valeurs par défaut du serveur, jamais configurées ici",
    awaitingConnector:
      "Enregistré. Ce plan de contrôle ne peut pas encore reconfigurer un moteur privé distant : appliquez-la sur le serveur de l’organisation, ou attendez le connecteur privé.",
    remoteUnsupported:
      "L’application à distance n’existe pas dans cette version. La configuration écrite ici ne s’applique que lorsque Nova tourne sur le serveur de l’organisation.",

    save: "Enregistrer la configuration",
    saving: "Enregistrement…",
    saved: "Configuration enregistrée.",
    unsaved: "Modifications non enregistrées.",
    discard: "Annuler les modifications",
    conflict:
      "Quelqu’un a modifié cette configuration pendant que vous la changiez. Rechargez pour voir l’état actuel.",
    reload: "Recharger",
    readOnly: "Votre rôle permet de lire cette page, mais pas de modifier le moteur.",
    hardwareIncompatible:
      "La mémoire graphique de ce serveur est trop petite pour le profil Avancé. Choisissez Standard ou Économe.",
  },

  configuration: {
    title: "Configuration",
    lead: "Les réglages techniques de cette organisation.",
    discoveryTitle: "Découverte de l’organisation",
    discoveryBody:
      "Permet à un poste de trouver cette organisation à partir de son identifiant, au lieu de recevoir une adresse.",
    discoveryEnabled: "Découverte activée",
    endpointInvalid: "L’adresse de service annoncée n’est pas acceptable :",
    disableDiscovery: "Désactiver la découverte",
    enableDiscovery: "Activer la découverte",
    save: "Enregistrer",
  },

  policies: {
    title: "Règles de l’application",
    lead: "Ce que les membres de cette organisation peuvent utiliser dans Nova.",
    boundary:
      "Ces réglages ne portent que sur les fonctions de l’application. Ils ne touchent jamais à la connexion, à l’accès des administrateurs ni au journal d’administration.",
    defaults: "Réglages par défaut : rien n’a encore été changé.",
    revision: "Révision",
    lastUpdated: "Dernière modification",
    never: "Jamais",
    readOnly: "Vous pouvez consulter ces réglages, mais pas les modifier.",
    unsaved: "Vous avez des modifications non enregistrées.",
    save: "Enregistrer",
    saving: "Enregistrement…",
    discard: "Annuler les modifications",
    saved: "Enregistré.",
    reload: "Recharger",
    conflict:
      "Les règles de l’organisation ont changé pendant que vous les modifiiez. Rechargez la dernière configuration avant d’enregistrer.",
    enabled: "Activé",
    disabled: "Désactivé",
    learningTitle: "Learn",
    learningBody: "Affiche la catégorie Learn, où les membres suivent les leçons de l’organisation.",
    restoreDefaults: "Rétablir les valeurs par défaut",
    restoreDefaultsHint: "Remplit les valeurs par défaut de Nova. Rien ne change avant l’enregistrement.",
    groupSidebar: "Catégories de la barre latérale",
    groupOptional: "Fonctions optionnelles",
    optionalNote:
      "Désactivée par défaut : Nova garde l’application simple pour les élèves tant que vous n’ouvrez pas une fonction.",
    meetingTitle: "Mode réunion",
    meetingBody:
      "Affiche la catégorie Réunion, qui capte les autres participants d’une réunion en ligne et en rédige le compte rendu.",
    groupOther: "Autres fonctions",
    groupMemberView: "Ce que voient les membres",
    memberGroupsTitle: "Montrer aux membres leurs groupes",
    memberGroupsBody:
      "Les membres voient les groupes auxquels ils appartiennent dans Nova Desktop, par exemple « Aéro 2 ». Masqués, aucun nom de groupe n’est envoyé à leur poste. Vous pouvez aussi choisir personne par personne depuis sa page de membre, ou masquer un seul groupe depuis la page Groupes.",
    sidebarNote: "Fermée, elle disparaît de la barre latérale du membre dans Nova.",
    stylesTitle: "Styles",
    stylesBody:
      "Affiche la catégorie Styles et distribue les Styles de cette organisation. Fermée, les dictées sont toujours mises au propre avec le Style par défaut.",
    personalStylesTitle: "Styles personnels",
    personalStylesBody:
      "Permet aux membres de créer leurs propres Styles. Les Styles intégrés et ceux de l’organisation restent disponibles.",
    generatedInstructionsTitle: "Prompts",
    generatedInstructionsBody: "Affiche la catégorie Prompts, où les membres retrouvent les prompts produits par leurs dictées.",
    pastDictationsTitle: "Historique",
    pastDictationsBody: "Affiche la catégorie Historique, où les membres retrouvent leurs dictées passées.",
    deviceCategoryNote:
      "La fermer masque la catégorie. Son contenu reste sur le poste de chaque membre et n’est jamais envoyé à l’organisation.",
    aiSkillsTitle: "AI Skills",
    aiSkillsBody: "Permet aux membres d’utiliser les AI Skills dans Nova.",
    vocabularyTitle: "Vocabulaire de l’organisation",
    vocabularyBody:
      "Permet aux membres d’utiliser le vocabulaire et le dictionnaire fournis par cette organisation.",
    commandsTitle: "Commandes Nova",
    commandsBody: "Permet aux membres de lancer des Commandes Nova sur le texte qu’ils sélectionnent.",
    notesTitle: "Notes structurées",
    notesBody: "Permet aux membres de transformer des dictées en notes structurées.",
    fileImportTitle: "Import de fichier audio",
    fileImportBody: "Permet aux membres de transcrire un fichier audio existant.",
    dictationSafe: "La dictée au micro reste toujours disponible.",
    modeRestricted:
      "Certaines fonctions peuvent rester indisponibles à cause du mode éducation de ce déploiement.",
  },

  usageMetrics: {
    title: "Usage",
    description:
      "Combien Nova sert, à combien de personnes, à quelle heure, et combien de temps elles attendent. La charge de la machine est affichée en dessous.",
    window: { "24": "24 heures", "168": "7 jours", "744": "31 jours" },
    dictations: "Dictées",
    peoplePeak: "Personnes au pic",
    estimatedCost: "Coût estimé de l’IA",
    dollars: (value: number) => `${value.toFixed(2).replace(".", ",")} $`,
    busiestHour: "Heure la plus chargée",
    latencyAverage: "Attente moyenne",
    latencyPeak: "Pire attente",
    hoursOverObjective: (count: number) =>
      count === 1 ? "1 heure au-delà de 5 s" : `${count} heures au-delà de 5 s`,
    dictationsByHour: "Dictées par heure",
    peopleByHour: "Personnes par heure",
    latencyByHour: "Attente par heure",
    objectiveLegend: "Pic et moyenne, face à l’objectif de 5 s",
    cpuAndMemory: "Processeur et mémoire",
    graphicsCard: "Carte graphique",
    percentLegend: "Pourcentage utilisé",
    noGraphicsCard:
      "Aucune carte graphique n’a été vue sur cette machine. Nova sert des modèles plus petits sur le seul processeur.",
    empty: "Aucune dictée sur cette période pour l’instant.",
    exportCsv: "Exporter cette période (CSV)",
    exportMembersCsv: "Exporter le détail nominatif (CSV)",
    showNames: "Afficher le détail nominatif",
    namesAreJournalled:
      "Ce détail nomme des membres. Chaque ouverture est inscrite au journal d’administration. Prévenez les personnes concernées que leur usage est mesuré.",
    member: "Membre",
  },
  adoption: {
    title: "Adoption",
    description:
      "Combien de personnes utilisent Nova chaque semaine, combien reviennent, et combien elles dictent. Des nombres seulement, jamais de noms : cette page peut figurer dans un rapport.",
    latestWeek: "Dernière semaine",
    people: "Personnes",
    regularPeople: (days: number) => `Régulières (${days} jours ou plus)`,
    dictations: "Dictées",
    minutes: "Minutes dictées",
    week: "Semaine",
    month: "Mois",
    byWeek: "Par semaine (12 dernières semaines)",
    byMonth: "Par mois",
    maskRule: (min: number) =>
      `Une semaine ou un mois où moins de ${min} personnes ont dicté est masqué. Quand on sait qui participe à un pilote, un nombre d’une ou deux personnes est leur propre usage.`,
    masked: (min: number) => `Masqué : moins de ${min} personnes`,
    notMeasured: "Pas encore mesuré",
    partial: "(partiel)",
    measuredFrom: (day: string) =>
      `L’usage est mesuré depuis le ${day}. Les semaines antérieures sont indiquées non mesurées, et la semaine ou le mois du début de la mesure est marqué partiel.`,
    notMeasuredYet: "Rien n’a encore été mesuré sur ce serveur.",
    empty: "Aucune dictée enregistrée pour l’instant.",
    exportCsv: "Exporter le rapport (CSV)",
  },
  limits: {
    title: "Limites",
    lead: "Les limites qui s’appliquent à chaque membre de cette organisation.",
    dictationTitle: "Durée d’une dictée",
    behaviour:
      "Nova prévient le membre 30 secondes avant la limite, puis arrête la dictée et transcrit ce qui a été enregistré. Rien n’est perdu.",
    current: "Limite actuelle",
    defaultValue: "Par défaut",
    minutes: (count: number) => (count === 1 ? "1 minute" : `${count} minutes`),
    fieldTitle: "Durée maximale d’une dictée (minutes)",
    range: (min: number, max: number) => `Minutes entières, de ${min} à ${max}.`,
    problems: {
      empty: "Saisissez un nombre de minutes.",
      whole: "Utilisez des minutes entières.",
      range: "La limite doit être comprise entre 1 et 120 minutes.",
    },
    groups:
      "Un groupe peut fixer sa propre limite dans Configuration, sous Réglages par groupe. Quand un membre appartient à plusieurs groupes, la limite la plus courte s’applique.",
    readOnly: "Vous pouvez consulter cette limite, mais pas la modifier.",
    capTitle: "Mises au propre par membre et par jour",
    capBehaviour:
      "Chaque mise au propre passe par le moteur d’IA. Au-delà du plafond, la dictée du membre est collée telle qu’elle a été dictée, sans mise au propre, jusqu’au lendemain. Rien n’est perdu.",
    capField: "Mises au propre par membre et par jour (0 = sans plafond)",
    capRange: (max: number) => `Nombre entier, de 0 à ${max}.`,
    capValue: (count: number) => (count === 0 ? "Sans plafond" : `${count} par jour`),
    capProblems: {
      empty: "Saisissez un nombre.",
      whole: "Utilisez un nombre entier.",
      range: "Le plafond doit être compris entre 0 et 10 000.",
    },
  },

  styles: {
    title: "Styles",
    lead: "Désactivez un seul Style pour tous les membres. Nova l’affiche comme désactivé par votre organisation, et les dictées passent par un autre Style.",
    builtin: "Intégrés à Nova",
    organization: "Publiés par votre organisation",
    enabled: "Activé",
    disabled: "Désactivé",
    readOnly: "Vous pouvez consulter ces Styles, mais pas les modifier.",
    conflict: "Les règles de l’organisation ont changé pendant que vous les modifiiez. Rechargez avant de changer un Style.",
    reload: "Recharger",
  },

  groupPolicies: {
    title: "Réglages par groupe",
    lead: "Un groupe peut changer certains réglages pour ses seuls membres. Tout le reste suit les règles de l’organisation ci-dessus.",
    restrictive:
      "Quand un membre appartient à plusieurs groupes qui ne s’accordent pas, la valeur la plus restrictive s’applique : désactivé plutôt qu’activé, et la limite de dictée la plus courte.",
    empty: "Cette organisation n’a encore aucun groupe.",
    followsOrganization: "Suit l’organisation",
    overrideCount: (count: number) =>
      count === 1 ? "1 réglage changé pour ce groupe" : `${count} réglages changés pour ce groupe`,
    edit: "Modifier",
    view: "Voir",
    close: "Fermer",
    inherit: "Suivre l’organisation",
    on: "Activé pour ce groupe",
    off: "Désactivé pour ce groupe",
    limits: "Limites",
    dictationTitle: "Durée maximale d’une dictée (minutes)",
    dictationBody: "Laissez vide pour suivre la limite de l’organisation.",
    save: "Enregistrer les réglages du groupe",
    saving: "Enregistrement…",
    cancel: "Annuler",
    resetGroup: "Tout aligner sur l’organisation",
    unsaved: "Vous avez des modifications non enregistrées.",
    readOnly: "Vous pouvez consulter ces réglages, mais pas les modifier.",
    conflict: "Les réglages de ce groupe ont changé pendant que vous les modifiiez. Rechargez avant d’enregistrer.",
    reload: "Recharger",
    notFound: "Ce groupe n’existe plus.",
  },

  packages: {
    title: "Paquets",
    lead: "Le contenu que cette organisation distribue à ses membres.",
    policyBoundary:
      "Publier un contenu ne donne aucun droit. Un paquet publié n’atteint les membres que si la règle correspondante est activée dans Configuration.",
    empty: "Cette organisation ne distribue encore aucun contenu.",
    readOnly: "Vous pouvez consulter ce contenu, mais pas le modifier.",
    createTitle: "Nouveau paquet",
    create: "Créer le paquet",
    type: "Type",
    name: "Nom",
    description: "Description",
    typeStyle: "Style",
    typeAiSkill: "AI Skill",
    typeVocabulary: "Vocabulaire",
    typeLabel: (value: string) =>
      value === "style"
        ? "Style"
        : value === "ai_skill"
          ? "AI Skill"
          : value === "vocabulary"
            ? "Vocabulaire"
            : value,
    status: "État",
    statusLabel: (value: string) =>
      value === "draft"
        ? "Brouillon"
        : value === "published"
          ? "Publié"
          : value === "archived"
            ? "Archivé"
            : value,
    active: "Active",
    incomplete: "Incomplet",
    completeFirst: "Remplissez les champs obligatoires avant de publier.",

    activeVersion: "Version active",
    versions: "Versions",
    noVersion: "Aucune version pour l’instant. Créez un brouillon pour ajouter du contenu.",
    open: "Ouvrir",
    close: "Fermer",
    newDraft: "Créer un brouillon",
    editDraft: "Modifier le brouillon",
    editing: "Modification",
    deleteDraft: "Supprimer le brouillon",
    archive: "Archiver",
    archiveTitle: "Archiver ce paquet ?",
    archiveBody:
      "Les membres ne reçoivent plus ce contenu. Rien n’est supprimé : chaque version est gardée, et vous pourrez restaurer le paquet.",
    restore: "Restaurer",
    delete: "Supprimer le paquet",
    deleteTitle: "Supprimer définitivement ce paquet ?",
    deleteBody:
      "Le paquet et toutes ses versions sont retirés. C’est irréversible. Pour le garder sans le distribuer, laissez-le archivé.",
    notArchived: "Archivez ce paquet avant de le supprimer.",
    archivedNote:
      "Ce paquet est archivé. Les membres ne le reçoivent plus. Restaurez-le pour y travailler de nouveau.",
    save: "Enregistrer le brouillon",
    discard: "Annuler les modifications",
    unsaved: "Vous avez des modifications non enregistrées.",
    publish: "Publier",
    publishTitle: "Publier cette version ?",
    publishBody: (version: number) =>
      `La version ${version} devient la version active et est distribuée aux membres de cette organisation. Publier et activer sont deux états : une version antérieure reste publiée mais inactive, et peut redevenir active pour revenir en arrière. Une version publiée ne se modifie jamais : pour changer le contenu, créez un nouveau brouillon.`,
    activate: "Rendre active",
    activateTitle: "Changer la version active ?",
    activateBody: (version: number) =>
      `Les membres recevront la version ${version}. Rien n’est réécrit : la version redevient active exactement telle qu’elle a été publiée.`,
    styleName: "Nom du Style",
    styleInstruction: "Consigne",
    skillTitle: "Titre de la compétence",
    skillSummary: "Résumé",
    skillInstruction: "Consigne (obligatoire)",
    skillInstructionHint: "Ce que le modèle doit faire du texte. C’est ce qui s’exécute.",
    skillPractice: "Conseil d’utilisation",
    skillPracticeHint: "Affiché aux membres. Pas envoyé au modèle.",
    skillDuration: "Durée (minutes)",
    skillSteps: "Étapes",
    addStep: "Ajouter une étape",
    vocabularyEntries: "Entrées",
    vocabularyTerm: "Terme",
    vocabularyReplacement: "Remplacement",
    addEntry: "Ajouter une entrée",
    removeRow: "Retirer",
  },

  learn: {
    title: "Learn",
    lead: "Choisissez les leçons que voient les membres, celles qui sont obligatoires et pour quand, et écrivez les vôtres.",
    readOnly: "Vous pouvez consulter les réglages de Learn, mais pas les modifier.",
    newLesson: "Nouvelle leçon",
    newTitle: "Nouvelle leçon",
    editTitle: "Modifier la leçon",
    close: "Fermer",
    emptyModule: "Aucune leçon dans ce module.",
    custom: "Écrite par votre organisation",
    archived: "Archivée",
    required: "Obligatoire",
    dueOn: (date: string) => `Pour le ${date}`,
    groupsAll: "Tout le monde",
    groupsSome: (count: number) => (count === 1 ? "1 groupe" : `${count} groupes`),
    settings: "Réglages",
    requiredLabel: "Leçon obligatoire",
    dueDate: "Date limite",
    groups: "Groupes",
    groupsHint: "Aucun groupe sélectionné : tous les membres.",
    saveSettings: "Enregistrer les réglages",
    save: "Enregistrer la leçon",
    saving: "Enregistrement…",
    cancel: "Annuler",
    edit: "Modifier",
    archive: "Archiver",
    archiveTitle: "Archiver cette leçon ?",
    archiveBody:
      "Les membres ne voient plus cette leçon. Rien n’est supprimé : leur progression est gardée, et restaurer la leçon la ramène telle qu’elle était.",
    restore: "Restaurer",
    delete: "Supprimer la leçon",
    deleteTitle: "Supprimer définitivement cette leçon ?",
    deleteBody:
      "La leçon, ses réglages et la progression de chaque membre sont retirés. C’est irréversible. Pour la masquer sans rien perdre, archivez-la.",
    module: "Module",
    sourceLanguage: "Langue d’origine",
    minutes: "Durée (minutes)",
    minutesShort: (minutes: number) => `${minutes} min`,
    difficulty: "Difficulté",
    difficultyLabel: (value: string) =>
      value === "beginner"
        ? "Débutant"
        : value === "intermediate"
          ? "Intermédiaire"
          : value === "advanced"
            ? "Avancé"
            : value,
    languages: "Langues",
    addLanguage: "Ajouter une langue…",
    removeLanguage: "Retirer cette langue",
    sourceBadge: "origine",
    translationNote:
      "Les blocs s’ajoutent, se déplacent et se retirent dans la langue d’origine. Ici, seul leur texte se traduit.",
    lessonTitle: "Titre",
    lessonDescription: "Description",
    blocks: "Blocs",
    addBlock: "Ajouter un bloc",
    blockType: "Type de bloc",
    blockLabel: (type: string) =>
      (
        ({
          text: "Texte",
          example: "Exemple",
          comparison: "Avant et après",
          tip: "Conseil",
          warning: "Attention",
          multiple_choice: "Quiz",
          question: "Quiz",
          reflection: "Réflexion",
          prompt_exercise: "Exercice d’écriture",
          takeaway: "À retenir",
        }) as Record<string, string>
      )[type] ?? type,
    moveUp: "Monter",
    moveDown: "Descendre",
    removeBlock: "Retirer",
    dragHint: "Faites glisser un bloc pour le déplacer, ou utilisez les flèches.",
    fieldLabel: (field: string) =>
      (
        ({
          heading: "Titre (facultatif)",
          body: "Texte",
          worse_label: "Étiquette de la version faible (facultatif)",
          worse: "Version faible",
          better_label: "Étiquette de la version forte (facultatif)",
          better: "Version forte",
          note: "Note (facultatif)",
          question: "Question",
          explanation: "Explication affichée après la réponse",
          prompt: "Question de réflexion",
          instruction: "Consigne",
          starting_text: "Texte de départ (facultatif)",
          hint: "Indice (facultatif)",
          sample_answer: "Exemple de réponse",
        }) as Record<string, string>
      )[field] ?? field,
    options: "Réponses",
    addOption: "Ajouter une réponse",
    removeOption: "Retirer",
    correct: "Bonne réponse",
    correctHint: "Sélectionnez la bonne réponse. Les membres voient l’explication après avoir répondu.",
    preview: "Aperçu",
    previewNote: "Tel que les membres le lisent dans cette langue. La bonne réponse n’est marquée qu’ici.",
    untitled: "Leçon sans titre",
    worseDefault: "Avant",
    betterDefault: "Après",
    problemsTitle: "Avant d’enregistrer",
    lessonWide: "Leçon",
    problemLabel: (field: string) =>
      (
        ({
          title: "le titre manque ou dépasse 120 caractères",
          description: "la description manque ou dépasse 400 caractères",
          blocks: "ajoutez entre 1 et 40 blocs",
          blocks_mismatch: "les blocs diffèrent de la langue d’origine",
          estimated_minutes: "la durée doit être comprise entre 1 et 10 minutes",
          difficulty: "choisissez une difficulté",
          module_id: "choisissez un module",
          missing_source: "la langue d’origine n’a pas de contenu",
          options: "chaque réponse a besoin d’un libellé",
          correct_option_id: "sélectionnez la bonne réponse",
        }) as Record<string, string>
      )[field] ?? "ce texte est vide",
    conflict: "Cette leçon a changé pendant que vous la modifiiez. Rechargez avant d’enregistrer.",
    reload: "Recharger",
    invalid: "Le serveur a refusé cette leçon. Vérifiez les champs indiqués et réessayez.",
    limit: "Cette organisation a atteint le nombre de leçons qu’elle peut écrire.",
    notFound: "Cette leçon n’existe plus.",
    customize: "Personnaliser",
    customizeTitle: "Personnaliser cette leçon ?",
    customizeBody:
      "Nova fait une copie de cette leçon, dans toutes les langues, que votre organisation peut modifier. La copie remplace l’original pour les membres : l’original est archivé et ses réglages passent à la copie. La progression sur l’original n’est pas reprise.",
    customized: "Personnalisée à partir d’une leçon Nova",
    updateAvailable: "Mise à jour de Nova disponible",
    updateTitle: "Nova a mis à jour la leçon d’origine",
    updateBody: (from: number, to: number) =>
      `Cette copie repose sur la version ${from}. Nova livre maintenant la version ${to}. Rien ne change avant votre décision.`,
    showUpdate: "Afficher la version de Nova",
    hideUpdate: "Masquer la version de Nova",
    applyUpdate: "Utiliser la version de Nova",
    applyTitle: "Utiliser la version de Nova ?",
    applyBody:
      "Le contenu de cette copie est remplacé par la version de Nova, dans toutes les langues. Vos modifications de cette leçon sont perdues, y compris celles non enregistrées. Ses réglages, ses groupes et sa date limite restent.",
    keepVersion: "Garder ma version",
    keepHint: "Garder votre version masque cette proposition jusqu’à ce que Nova en livre une plus récente.",
    notCustomizable:
      "Cette leçon contient un exercice d’IA, que seul Nova peut fournir. Elle ne peut pas être personnalisée.",
    updateNotAvailable: "Nova n’a pas de version plus récente de cette leçon. Rechargez pour voir son état actuel.",
  },

  security: {
    title: "Sécurité",
    lead: "Comment l’administration de cette organisation est protégée.",
    yourRole: "Votre rôle",
    capabilities: "Vos droits",
    sessionExpiry: "La session d’administration expire dans",
    modernAuth: "Authentification moderne des administrateurs",
    modernAuthOn: "En place : connexion par le fournisseur d’identité, avec nouvelle authentification",
    stepUp: "Nouvelle authentification",
    stepUpDone: "Faite à l’ouverture de cette session",
    mfaTitle: "Authentification multifacteur",
    mfaBody:
      "Nova ne vérifie pas lui-même de second facteur, et ne sait pas quelle méthode a été utilisée. Votre fournisseur d’identité applique sa propre politique, accès conditionnel compris.",
    auditTitle: "Activité d’administration récente",
    auditBody:
      "Chaque changement d’administration est inscrit sur le serveur. Cette vue est en lecture seule : aucune interface ne peut la modifier ni l’effacer.",
    auditEmpty: "Aucune activité d’administration inscrite pour l’instant.",
    auditWhen: "Quand",
    auditWho: "Qui",
    auditWhat: "Quoi",
    auditTarget: "Cible",
    auditMore: "Charger l’activité plus ancienne",
    auditLegacyActor: "ancien jeton d’administration",
    auditEnabled: "Activé",
    auditDisabled: "Désactivé",
    auditNoValue: "—",
    auditIntegrity:
      "Les inscriptions ne font que s’ajouter par l’application. Quiconque a accès au fichier de base de données du serveur peut encore les modifier.",
  },

  diagnostics: {
    title: "Diagnostic",
    lead: "La santé du service de cette organisation. Aucun contenu des personnes n’est jamais affiché ici.",
    api: "API",
    apiReachable: "Joignable",
    apiUnreachable: "Injoignable",
    discovery: "Découverte",
    discoveryReachable: "Résout",
    providers: "Fournisseurs",
    version: "Version",
    boundary:
      "Nova Admin gère la configuration. L’audio dicté, les transcriptions et les consignes n’y passent jamais.",
  },

  errors: {
    userNotFound: "Ce compte n’existe plus.",
    memberStatusInvalid: "Cet état n’est pas reconnu.",
    memberRoleInvalid: "Ce type de membre n’est pas reconnu.",
    securityRoleInvalid: "Ce rôle n’est pas reconnu.",
    concurrencyOutOfRange: "Ces valeurs de simultanéité sont hors limites. Rechargez et réessayez.",
    profileInvalid: "Ce profil de modèle n’est pas reconnu.",
    policyKeyUnknown: "Cette console a envoyé un réglage que le service Nova ne connaît pas. Mettez Nova Admin à jour.",
    policyValueInvalid: "Un de ces réglages a une valeur inattendue. Rechargez et réessayez.",
    network: "Impossible de joindre le service Nova. Vérifiez votre connexion et réessayez.",
    unauthenticated: "Votre session est terminée. Connectez-vous de nouveau.",
    stepUpRequired: "Administrer demande une connexion récente.",
    adminSessionExpired: "Votre session d’administration est terminée. Authentifiez-vous de nouveau pour continuer.",
    forbidden: "Votre compte n’a pas le droit de faire cela.",
    notFound: "Cette organisation n’est pas disponible.",
    conflict: "Ce changement entre en conflit avec la configuration actuelle.",
    invalid: "Le serveur a refusé ces valeurs. Vérifiez les champs et réessayez.",
    contractMismatch: "Cette console est plus ancienne que le service Nova auquel elle parle. Mettez Nova Admin à jour.",
    server: "Le service Nova n’a pas pu traiter cette demande.",
  },
};
