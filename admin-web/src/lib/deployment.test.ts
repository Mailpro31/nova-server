import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CAPABILITY, can, initialSession, type SessionState } from "./session";
import { t } from "./messages";

const PAGE = readFileSync(
  new URL("../pages/DesktopDeployment.tsx", import.meta.url),
  "utf8",
);
const CONTROL = readFileSync(new URL("./controlPlane.ts", import.meta.url), "utf8");

function sessionWith(capabilities: string[], role = "it_admin"): SessionState {
  return {
    ...initialSession,
    phase: "administering",
    admin: {
      role,
      capabilities,
      organizationId: "organization-test",
      expiresAt: Date.now() + 60_000,
    },
  };
}

describe("Windows deployment surface", () => {
  test("deployment mutations are capability-gated, never role-gated", () => {
    expect(can(sessionWith([]), CAPABILITY.deploymentManage)).toBe(false);
    expect(
      can(sessionWith(["deployment_manage"]), CAPABILITY.deploymentManage),
    ).toBe(true);
    // Un `read_only` porteur de `diagnostics_read` voit la page et ne peut rien
    // changer : c'est la capacite qui decide, pas le nom du role.
    expect(can(sessionWith(["diagnostics_read"], "read_only"), CAPABILITY.deploymentManage)).toBe(
      false,
    );
    expect(PAGE).toContain("CAPABILITY.deploymentManage");
    expect(PAGE).not.toContain('role === "it_admin"');
  });

  test("the install command shown to an admin carries no durable secret", () => {
    // La commande vient du serveur et n'est jamais reconstruite ici : la page
    // ne peut donc pas y glisser autre chose que ce que le Control Plane a
    // produit.
    expect(PAGE).toContain("view.install_commands[profile.profile_id]");
    for (const forbidden of ["token", "secret", "password", "client_secret"]) {
      expect(PAGE.toLowerCase()).not.toContain(`${forbidden}:`);
    }
  });

  test("no deployment state is written to browser storage", () => {
    expect(PAGE).not.toContain("localStorage");
    expect(PAGE).not.toContain("sessionStorage");
  });

  test("the page never offers an arbitrary server address", () => {
    // L'origine du Control Plane est epinglee dans le package : la console
    // l'affiche, elle ne la choisit pas.
    expect(PAGE).toContain("view.control_plane_origin");
    expect(PAGE).not.toContain("setOrigin");
    expect(PAGE).not.toContain("server_url");
    // L'origine est un champ de lecture. Aucune methode d'ecriture ne
    // l'accepte : le corps d'une creation ou d'une mise a jour ne la mentionne
    // nulle part, donc la console ne peut pas rediriger un parc.
    const writeMethods = CONTROL.slice(
      CONTROL.indexOf("createDeploymentProfile("),
      CONTROL.indexOf("deploymentProfileConfig("),
    );
    expect(writeMethods.length).toBeGreaterThan(100);
    expect(writeMethods).not.toContain("control_plane_origin");
    expect(writeMethods).not.toContain("server_url");
  });

  test("an admin cannot upload an installer from the console", () => {
    expect(PAGE).not.toContain("<input type=\"file\"");
    expect(CONTROL).not.toContain("uploadInstaller");
    expect(CONTROL).not.toContain("desktop-releases");
  });

  test("the console never claims a fleet health it has not measured", () => {
    // `healthy` n'existe pas dans le contrat cote console : il exigerait un
    // rapport de poste, et Nova n'en recoit aucun.
    // Le contrat d'etat du deploiement n'a pas de `healthy` : il exigerait un
    // rapport de poste. (`healthy` existe ailleurs dans le fichier, pour le
    // runtime IA, qui lui rapporte vraiment son etat.)
    const deploymentView = CONTROL.slice(
      CONTROL.indexOf("export interface DeploymentView"),
      CONTROL.indexOf("export interface ManagedConfigView"),
    );
    expect(deploymentView.length).toBeGreaterThan(200);
    expect(deploymentView).not.toContain("healthy");
    expect(deploymentView).toContain("device_reporting");
    expect(PAGE).toContain("device_reporting");
    expect(t.deployment.fleetBody.toLowerCase()).toContain("do not report");
  });

  test("an unverified release is labelled as such rather than assumed signed", () => {
    expect(PAGE).toContain("view.release.signed");
    expect(PAGE).toContain("t.deployment.releaseUnsigned");
    expect(t.deployment.releaseNone.toLowerCase()).toContain("not");
  });

  test("only the channels and policies the workstation accepts are offered", () => {
    for (const channel of ["stable", "preview"]) {
      expect(PAGE).toContain(`value="${channel}"`);
    }
    for (const policy of ["manual", "notify", "automatic"]) {
      expect(PAGE).toContain(`value="${policy}"`);
    }
    // Rien d'autre : un canal invente serait refuse par le serveur et par le
    // poste, et l'offrir ici ne produirait qu'une erreur.
    expect(PAGE).not.toContain('value="nightly"');
    expect(PAGE).not.toContain('value="forced"');
  });

  test("every deployment label used by the page exists", () => {
    const used = [...PAGE.matchAll(/t\.deployment\.([A-Za-z]+)/g)].map((m) => m[1]);
    expect(used.length).toBeGreaterThan(10);
    for (const key of new Set(used)) {
      expect(t.deployment).toHaveProperty(key);
    }
  });
});
