/**
 * Modèle de session : capacités, transitions, absence de stockage persistant.
 */

import { describe, expect, test } from "bun:test";
import {
  CAPABILITY,
  can,
  initialSession,
  secondsRemaining,
  sessionAfterFailure,
  type SessionState,
} from "./session";

function administering(capabilities: string[], role = "organization_admin"): SessionState {
  return {
    ...initialSession,
    phase: "administering",
    organizationSlug: "ecole-exemple",
    serviceEndpoint: "https://nova.example.com",
    userToken: "jeton-utilisateur",
    adminToken: "jeton-admin",
    admin: {
      role,
      capabilities,
      organizationId: "org-1",
      expiresAt: Date.now() + 3_600_000,
    },
  };
}

describe("capacités", () => {
  test("l'interface se règle sur les capacités, pas sur le nom du rôle", () => {
    const readOnly = administering(
      [CAPABILITY.organizationRead, CAPABILITY.diagnosticsRead],
      "read_only",
    );
    expect(can(readOnly, CAPABILITY.organizationRead)).toBe(true);
    expect(can(readOnly, CAPABILITY.providerManage)).toBe(false);
    expect(can(readOnly, CAPABILITY.discoveryManage)).toBe(false);
  });

  test("it_admin gère la machine, pas les personnes", () => {
    const itAdmin = administering(
      [
        CAPABILITY.organizationRead,
        CAPABILITY.providerManage,
        CAPABILITY.discoveryManage,
        CAPABILITY.deploymentManage,
        CAPABILITY.diagnosticsRead,
      ],
      "it_admin",
    );
    expect(can(itAdmin, CAPABILITY.providerManage)).toBe(true);
    expect(can(itAdmin, CAPABILITY.deploymentManage)).toBe(true);
    expect(can(itAdmin, CAPABILITY.identityManage)).toBe(false);
    expect(can(itAdmin, CAPABILITY.securityManage)).toBe(false);
  });

  test("organization_admin gère les personnes, pas le déploiement", () => {
    const orgAdmin = administering([
      CAPABILITY.organizationRead,
      CAPABILITY.providerManage,
      CAPABILITY.discoveryManage,
      CAPABILITY.identityManage,
      CAPABILITY.securityManage,
      CAPABILITY.diagnosticsRead,
    ]);
    expect(can(orgAdmin, CAPABILITY.identityManage)).toBe(true);
    expect(can(orgAdmin, CAPABILITY.deploymentManage)).toBe(false);
  });

  test("sans session d'administration, aucune capacité", () => {
    expect(can(initialSession, CAPABILITY.organizationRead)).toBe(false);
  });

  test("une capacité inconnue n'est jamais accordée", () => {
    expect(can(administering([CAPABILITY.providerManage]), "tout_faire")).toBe(false);
  });
});

describe("transitions", () => {
  test("une session d'administration expirée renvoie au step-up, sans boucle", () => {
    const next = sessionAfterFailure(
      administering([CAPABILITY.organizationRead]),
      "admin_session_expired",
    );
    expect(next.phase).toBe("step_up_required");
    expect(next.adminToken).toBeNull();
    expect(next.admin).toBeNull();
    // La session utilisateur survit : inutile de refaire tout le SSO.
    expect(next.userToken).toBe("jeton-utilisateur");
    // Rejouer la transition ne fait pas osciller l'état.
    expect(sessionAfterFailure(next, "admin_session_expired").phase).toBe(
      "step_up_required",
    );
  });

  test("une session utilisateur perdue ramène à la connexion", () => {
    const next = sessionAfterFailure(
      administering([CAPABILITY.organizationRead]),
      "unauthenticated",
    );
    expect(next.phase).toBe("signed_out");
    expect(next.userToken).toBeNull();
    expect(next.adminToken).toBeNull();
  });

  test("un échec réseau ne déconnecte personne", () => {
    const session = administering([CAPABILITY.organizationRead]);
    expect(sessionAfterFailure(session, "network")).toEqual(session);
  });

  test("l'organisation reste connue après un échec", () => {
    const next = sessionAfterFailure(
      administering([CAPABILITY.organizationRead]),
      "unauthenticated",
    );
    expect(next.organizationSlug).toBe("ecole-exemple");
    expect(next.serviceEndpoint).toBe("https://nova.example.com");
  });
});

describe("durée de session", () => {
  test("jamais négative", () => {
    const expired = administering([]).admin!;
    expect(secondsRemaining({ ...expired, expiresAt: Date.now() - 10_000 })).toBe(0);
  });

  test("comptée en secondes", () => {
    const admin = administering([]).admin!;
    const now = Date.now();
    expect(secondsRemaining({ ...admin, expiresAt: now + 120_000 }, now)).toBe(120);
  });
});

describe("stockage", () => {
  async function strippedSources(exclude: string[] = []) {
    const { readdirSync, readFileSync, statSync } = await import("node:fs");
    const { join, basename } = await import("node:path");

    function sources(dir: string): string[] {
      return readdirSync(dir).flatMap((entry) => {
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) return sources(full);
        return /\.tsx?$/.test(entry) && !entry.endsWith(".test.ts") ? [full] : [];
      });
    }

    return sources("src")
      .filter((file) => !exclude.includes(basename(file)))
      .map((file) => readFileSync(file, "utf8"))
      .join(String.fromCharCode(10))
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");
  }

  test("aucun stockage persistant, nulle part", async () => {
    // `localStorage` survit a la fermeture du navigateur : rien n'y a sa place.
    expect(await strippedSources()).not.toContain("localStorage");
    expect(await strippedSources()).not.toContain("document.cookie");
  });

  test("sessionStorage n'est utilise que par le sas de transit", async () => {
    // La redirection vers le fournisseur d'identite detruit la page : le
    // `code_verifier` et l'identifiant de tentative doivent survivre a cet
    // aller-retour. C'est le seul usage tolere, et il est confine a un module.
    const elsewhere = await strippedSources(["handoff.ts"]);
    expect(elsewhere).not.toContain("sessionStorage");
  });

  test("le jeton d'administration ne passe jamais par le sas", async () => {
    // La propriete qui compte : un script qui lirait ce stockage n'y trouverait
    // jamais de quoi administrer.
    const { readFileSync } = await import("node:fs");
    const handoff = readFileSync("src/lib/handoff.ts", "utf8");
    expect(handoff).not.toContain("adminToken");
    expect(handoff).not.toContain("admin.token");
    // Et le sas est vide des qu'il est lu.
    expect(handoff).toContain("removeItem");
  });

  test("le client n'envoie aucun identifiant ambiant", async () => {
    expect(await strippedSources()).toContain('credentials: "omit"');
  });
});
