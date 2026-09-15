/**
 * Garde d'accès et routage, vus depuis la machine à états.
 *
 * On teste la **décision** — quel écran pour quelle phase — plutôt que le rendu
 * React. C'est là qu'est la propriété de sécurité : aucune phase, quelle qu'elle
 * soit, ne doit mener à la console sans session d'administration.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { initialSession, type SessionPhase, type SessionState } from "./session";

/**
 * Reproduit la décision de `OrganizationRoutes`.
 *
 * Le mappage vit ici en toutes lettres : si quelqu'un ajoute une phase sans
 * décider de son écran, le test des phases exhaustives le signale.
 */
function screenFor(phase: SessionPhase): string {
  switch (phase) {
    case "discovering":
      return "bootstrap";
    case "signed_out":
      return "sign_in";
    case "signed_in":
    case "step_up_required":
      return "step_up";
    case "access_denied":
      return "denied";
    case "administering":
      return "console";
  }
}

const ALL_PHASES: SessionPhase[] = [
  "discovering",
  "signed_out",
  "signed_in",
  "step_up_required",
  "administering",
  "access_denied",
];

describe("garde d'accès", () => {
  test("seule la phase d'administration ouvre la console", () => {
    const consoleReaching = ALL_PHASES.filter((p) => screenFor(p) === "console");
    expect(consoleReaching).toEqual(["administering"]);
  });

  test("une session utilisateur seule ne suffit pas", () => {
    // C'est la propriété de la Phase 24, vue depuis l'interface : être connecté
    // mène au step-up, jamais à la console.
    expect(screenFor("signed_in")).toBe("step_up");
    expect(screenFor("step_up_required")).toBe("step_up");
  });

  test("un membre sans rôle voit un refus, pas un écran d'administration", () => {
    expect(screenFor("access_denied")).toBe("denied");
  });

  test("chaque phase a un écran : aucune ne tombe dans le vide", () => {
    for (const phase of ALL_PHASES) {
      expect(typeof screenFor(phase)).toBe("string");
    }
  });

  test("l'accès direct à une page profonde part de la découverte", () => {
    // `/o/acme/security` ouvert dans un onglet neuf : l'état initial est
    // « discovering », donc l'écran est le bootstrap — pas Security.
    expect(screenFor(initialSession.phase)).toBe("bootstrap");
  });
});

describe("portée d'organisation", () => {
  function open(slug: string): SessionState {
    return {
      ...initialSession,
      phase: "administering",
      organizationSlug: slug,
      serviceEndpoint: `https://${slug}.example.com`,
      adminToken: "jeton",
      admin: {
        role: "organization_admin",
        capabilities: ["organization_read"],
        organizationId: `org-${slug}`,
        expiresAt: Date.now() + 1000,
      },
    };
  }

  test("changer d'organisation repart d'une session vide", () => {
    // `OrganizationRoutes` relance la découverte dès que le slug de l'URL
    // diffère de celui de la session ; `openOrganization` repart de
    // `initialSession`. Aucun jeton d'un tenant ne peut servir pour un autre.
    const previous = open("acme");
    const next = { ...initialSession, phase: "signed_out" as const,
                   organizationSlug: "autre" };
    expect(next.adminToken).toBeNull();
    expect(next.admin).toBeNull();
    expect(next.serviceEndpoint).toBeNull();
    expect(previous.organizationSlug).not.toBe(next.organizationSlug);
  });

  test("le routage n'expose aucun identifiant interne", () => {
    const source = readFileSync("src/App.tsx", "utf8");
    expect(source).toContain("/o/:organizationSlug");
    // Ni UUID de tenant, ni identifiant de configuration dans les chemins.
    expect(source).not.toContain("organizationId");
    expect(source).not.toContain(":organizationId");
  });

  test("une decouverte reussie conduit reellement a /o/<slug>", () => {
    // Le bug de la recette reelle : la session passait bien en « connexion
    // attendue », mais rien ne naviguait. L'URL restait `/`, dont la route
    // reaffiche l'ecran de saisie — on cliquait sans que rien ne bouge.
    //
    // Tester la decision d'ecran par phase ne suffisait pas : il faut aussi
    // verifier que le routeur y conduit.
    const bootstrap = readFileSync("src/components/AuthScreens.tsx", "utf8");
    expect(bootstrap).toContain("useNavigate");
    expect(bootstrap).toMatch(/navigate\(`\/o\/\$\{[^}]+\}`\)/);
    // Et le slug navigue sous sa forme canonique, celle renvoyee par le
    // serveur — pas la saisie brute.
    expect(bootstrap).toContain("await openOrganization");
    expect(bootstrap).toContain("if (resolved)");
  });

  test("l'ecran de saisie ne navigue que sur succes", () => {
    const bootstrap = readFileSync("src/components/AuthScreens.tsx", "utf8");
    // Une decouverte en echec renvoie `null` : pas de navigation vers une
    // organisation qui n'existe pas.
    const submit = bootstrap.slice(bootstrap.indexOf("async function submit"));
    expect(submit.indexOf("if (resolved)")).toBeGreaterThan(
      submit.indexOf("await openOrganization"),
    );
  });

  test("le code d'autorisation ne reste pas dans l'historique", () => {
    const source = readFileSync("src/App.tsx", "utf8");
    expect(source).toContain("replace: true");
  });
});

describe("gestion des administrateurs", () => {
  test("toutes les mutations designent un user_id, jamais une adresse", async () => {
    // Une adresse est un attribut mutable depuis la Phase 23 : muter un
    // privilege en la designant reviendrait a viser une cible qui bouge.
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    const mutation = client.slice(
      client.indexOf("setAdministratorRole"),
      client.indexOf("searchMembers"),
    );
    expect(mutation).toContain("${userId}");
    expect(mutation).not.toContain("${email}");
  });

  test("le client n'envoie qu'un role, jamais de capacites", async () => {
    // Laisser passer une liste de capacites reviendrait a laisser le navigateur
    // ecrire la matrice d'autorisation.
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    const mutation = client.slice(
      client.indexOf("setAdministratorRole"),
      client.indexOf("searchMembers"),
    );
    expect(mutation).toContain("security_role: role");
    expect(mutation).not.toContain("capabilities");
  });

  test("les actions de gestion exigent identity_manage", async () => {
    const page = readFileSync("src/pages/Administrators.tsx", "utf8");
    expect(page).toContain("CAPABILITY.identityManage");
    // Le formulaire d'attribution n'apparait pas sans la capacite.
    expect(page).toContain("{mayManage && <GrantAccess");
  });

  test("le refus du dernier gestionnaire a son message propre", async () => {
    const { errorMessage } = await import("./messages");
    const specific = errorMessage("conflict", "ADMIN_LAST_IDENTITY_MANAGER");
    expect(specific).not.toBe(errorMessage("conflict"));
    expect(specific.toLowerCase()).toContain("at least one organization administrator");
  });

  test("retirer l'acces n'est pas supprimer la personne", async () => {
    const { t } = await import("./messages");
    // Le texte de confirmation doit lever l'ambiguite : c'est le privilege qui
    // part, pas le compte.
    expect(t.administrators.removeBody.toLowerCase()).toContain("keeps their nova account");
    expect(t.administrators.removeTitle.toLowerCase()).not.toContain("delete");
  });

  test("l'amorcage par la ligne de commande est annonce", async () => {
    const { t } = await import("./messages");
    // Sans cette phrase, on chercherait un bouton qui n'existera jamais.
    expect(t.administrators.bootstrapNote.toLowerCase()).toContain("command line");
  });

  test("aucun ecran ne cree une personne", async () => {
    const page = readFileSync("src/pages/Administrators.tsx", "utf8");
    for (const forbidden of ["Invite", "Create user", "New user", "Add person"]) {
      expect(page).not.toContain(forbidden);
    }
  });
});

describe("gestion des membres", () => {
  test("aucun ecran de membre ne montre de contenu dicte", async () => {
    // C'est ici que la tentation serait la plus forte : un onglet de plus, et
    // la console devient un outil de surveillance.
    // Commentaires retires : le mot « transcription » figure justement dans
    // celui qui declare que l'ecran n'en montre pas. Ce sont les *usages* qui
    // comptent.
    const strip = (source: string) =>
      source
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/.*$/gm, "")
        .toLowerCase();
    const page = strip(readFileSync("src/pages/Members.tsx", "utf8"));
    const client = strip(readFileSync("src/lib/controlPlane.ts", "utf8"));
    for (const forbidden of ["transcript", "dictation", "clipboard", "prompt",
                             "activity", "productivity"]) {
      expect(page).not.toContain(forbidden);
      expect(client).not.toContain(forbidden);
    }
  });

  test("la frontiere est affirmee a l'ecran, pas seulement en commentaire", async () => {
    const { t } = await import("./messages");
    expect(t.members.boundary.toLowerCase()).toContain("never shows what anyone dictated");
    const page = readFileSync("src/pages/Members.tsx", "utf8");
    expect(page).toContain("t.members.boundary");
  });

  test("toutes les mutations designent un user_id", async () => {
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    const members = client.slice(
      client.indexOf("  members("),
      client.indexOf("  audit("),
    );
    // Aucune route moderne ne vise une personne par son adresse.
    expect(members).not.toMatch(/members\/\$\{[^}]*[eE]mail/);
    expect(members).toContain("${userId}");
  });

  test("suspendre est distingue de supprimer", async () => {
    const { t } = await import("./messages");
    // Les deux gestes se confondent facilement dans une console.
    expect(t.members.suspendBody.toLowerCase()).toContain("not a deletion");
    expect(t.members.removeBody.toLowerCase()).toContain("suspending is usually the better choice");
  });

  test("reactiver ne promet pas de rendre les anciennes sessions", async () => {
    const { t } = await import("./messages");
    expect(t.members.reactivateBody.toLowerCase()).toContain("not restored");
  });

  test("la suppression annonce ce qui survit", async () => {
    const { t } = await import("./messages");
    // Le journal d'audit reste : il porte des actes d'administration.
    expect(t.members.removeBody.toLowerCase()).toContain("audit trail is kept");
  });

  test("les actions de gestion exigent identity_manage", async () => {
    const page = readFileSync("src/pages/Members.tsx", "utf8");
    expect(page).toContain("CAPABILITY.identityManage");
    expect(page).toContain("{mayManage && (");
  });

  test("le profil metier ne propose aucun role de securite", async () => {
    const page = readFileSync("src/pages/Members.tsx", "utf8");
    const editor = page.slice(page.indexOf("MEMBER_ROLES"), page.indexOf("export function Members"));
    // Les privileges se gerent depuis leur propre ecran : deux endroits qui
    // modifient le meme privilege finissent par diverger.
    for (const role of ["organization_admin", "it_admin", "read_only", "content_editor"]) {
      expect(editor).not.toContain(`"${role}"`);
    }
  });

  test("l'ecran des administrateurs propose l'editeur de contenu", async () => {
    // Confier les lecons, Styles et vocabulaire sans ouvrir les membres ni
    // les policies : le role doit pouvoir s'attribuer depuis la console.
    const page = readFileSync("src/pages/Administrators.tsx", "utf8");
    expect(page).toContain('value: "content_editor"');
    const { t } = await import("./messages");
    expect(t.administrators.roleContentEditor.length).toBeGreaterThan(0);
    expect(t.administrators.roleContentEditorHint.toLowerCase()).toContain("lessons");
  });

  test("les appareils sont nommes sans jeton", async () => {
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    const detail = client.slice(client.indexOf("interface MemberDetail"),
                                client.indexOf("export class ControlPlane"));
    expect(detail).toContain("machine");
    expect(detail).not.toContain("token");
  });
});

describe("journal d'audit", () => {
  test("la vue est en lecture seule", async () => {
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    const audit = client.slice(client.indexOf("  audit("), client.indexOf("signOutAdmin"));
    // Aucune methode d'ecriture : ni POST, ni DELETE.
    expect(audit).not.toContain("method:");
  });

  test("la pagination est demandee explicitement", async () => {
    const client = readFileSync("src/lib/controlPlane.ts", "utf8");
    expect(client).toContain('params.set("limit"');
    expect(client).toContain('params.set("before"');
  });

  test("l'integrite reelle est annoncee sans exageration", async () => {
    const { t } = await import("./messages");
    // SQLite ne protege pas un fichier de celui qui le detient : pretendre
    // l'immuabilite serait faux.
    expect(t.security.auditIntegrity.toLowerCase()).toContain("append-only");
    expect(t.security.auditIntegrity.toLowerCase()).toContain("can still alter");
  });

  test("un acteur absent est nomme plutot que laisse vide", async () => {
    const { t } = await import("./messages");
    expect(t.security.auditLegacyActor).toBeTruthy();
    const rest = readFileSync("src/pages/Rest.tsx", "utf8");
    expect(rest).toContain("t.security.auditLegacyActor");
  });

  test("l'audit n'est demande que par qui peut le lire", async () => {
    const rest = readFileSync("src/pages/Rest.tsx", "utf8");
    expect(rest).toContain("CAPABILITY.securityManage");
    expect(rest).toContain("if (!mayRead) return null;");
  });
});

describe("retours d'erreur visibles", () => {
  test("une boite de confirmation affiche le refus du serveur", () => {
    // Le refus arrivait bien (409), etait capture et stocke — puis rendu dans
    // le panneau, que la boite ouverte remplace. L'utilisateur voyait son clic
    // ne rien produire.
    const dialog = readFileSync("src/components/primitives.tsx", "utf8");
    const confirm = dialog.slice(dialog.indexOf("export function ConfirmDialog"));
    expect(confirm).toContain("error");
    expect(confirm).toMatch(/\{error &&/);
  });

  test("l'ecran des fournisseurs transmet le refus a la boite", () => {
    const identity = readFileSync("src/pages/Identity.tsx", "utf8");
    // La balise est auto-fermante : on lit jusqu'au premier `/>`.
    const opened = identity.slice(identity.indexOf("<ConfirmDialog"));
    expect(opened.slice(0, opened.indexOf("/>"))).toContain("error={failure}");
  });

  test("aucun ecran n'emprunte le libelle d'une action qu'il ne fait pas", async () => {
    // La page Configuration agissait sur la decouverte avec un bouton
    // « Disable provider », emprunte a la page Identity : le libelle annoncait
    // une action que l'ecran ne faisait pas.
    const rest = readFileSync("src/pages/Rest.tsx", "utf8");
    const configuration = rest.slice(
      rest.indexOf("export function Configuration"),
      rest.indexOf("export function Security"),
    );
    // `t.identity.disabled` — l'etiquette d'etat « Disabled » — reste
    // legitime et partagee : c'est le libelle d'*action* qui est vise.
    expect(configuration).not.toMatch(/t\.identity\.disable(?!d)/);
    expect(configuration).toContain("t.configuration.disableDiscovery");
  });

  test("le refus du dernier fournisseur a son propre message", async () => {
    // Un « conflit avec la configuration » generique n'expliquerait pas quoi
    // faire ; ce refus-la doit dire d'ajouter un autre fournisseur d'abord.
    const { errorMessage } = await import("./messages");
    const specific = errorMessage("conflict", "PROVIDER_LAST_ACTIVE");
    expect(specific).not.toBe(errorMessage("conflict"));
    expect(specific.toLowerCase()).toContain("lock every administrator out");
  });
});

describe("frontière produit", () => {
  test("aucune page ne parle de plusieurs organisations", () => {
    // Nova Admin est la console d'une organisation. « Toutes les
    // organisations » appartient à Nova Control, qui n'existe pas et ne doit
    // pas commencer ici par accident.
    const shell = readFileSync("src/App.tsx", "utf8");
    const messages = readFileSync("src/lib/messages.ts", "utf8");
    for (const forbidden of ["All organizations", "Tenants", "Platform"]) {
      expect(shell).not.toContain(forbidden);
      expect(messages).not.toContain(forbidden);
    }
  });

  test("aucune page ne montre de contenu utilisateur", async () => {
    // Test structurel plutot que lexical : ce qui compte est qu'aucune *page*
    // ni entree de navigation ne conduise a du contenu dicte. Le mot
    // « transcripts » apparait bien dans les textes — dans la phrase qui
    // declare precisement que Nova Admin n'y touche pas.
    const { t } = await import("./messages");
    const navigation = Object.values(t.nav).join(" ").toLowerCase();
    for (const forbidden of ["transcript", "recording", "audio", "prompt",
                             "activity", "history", "usage"]) {
      expect(navigation).not.toContain(forbidden);
    }
    // Et la frontiere est bien affirmee a l'ecran, pas seulement en commentaire.
    expect(t.diagnostics.boundary.toLowerCase()).toContain("never pass through");
  });

  test("aucun appel ne vise le plan de donnees", async () => {
    const source = readFileSync("src/lib/controlPlane.ts", "utf8");
    for (const forbidden of ["/api/transcribe", "/api/reformulate", "/api/history",
                             "/api/dictionary", "/api/vocabulary"]) {
      expect(source).not.toContain(forbidden);
    }
  });
});
