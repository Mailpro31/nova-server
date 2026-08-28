/**
 * L'écran Deployment → Private Connector, vu depuis ses garanties.
 *
 * On teste la décision plutôt que le rendu : qui peut enrôler et révoquer, ce
 * que la console affiche du code d'enrôlement, ce qu'elle refuse de garder, et
 * ce qu'elle refuse de promettre.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CAPABILITY, can, initialSession, type SessionState } from "./session";

const PAGE = readFileSync("src/pages/Connector.tsx", "utf8");
const REST = readFileSync("src/pages/Rest.tsx", "utf8");
const CONTROL = readFileSync("src/lib/controlPlane.ts", "utf8");

/** Le code, commentaires retirés : un mot trouvé dans un commentaire ne prouve
 *  rien sur ce que la console fait. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

function sessionWith(capabilities: string[]): SessionState {
  return {
    ...initialSession,
    phase: "administering",
    admin: {
      capabilities,
      role: "it_admin",
      expiresAt: Date.now() + 600_000,
    },
  } as unknown as SessionState;
}

describe("Capability boundary", () => {
  test("enrolling and revoking are gated on deployment_manage", () => {
    expect(code(PAGE)).toContain("CAPABILITY.deploymentManage");
    expect(code(PAGE)).not.toContain('role === "it_admin"');
  });

  test("a read-only session cannot enroll or revoke", () => {
    expect(
      can(sessionWith(["organization_read", "diagnostics_read"]), CAPABILITY.deploymentManage),
    ).toBe(false);
    expect(can(sessionWith(["deployment_manage"]), CAPABILITY.deploymentManage)).toBe(
      true,
    );
  });

  test("both write actions are behind the same gate", () => {
    const source = code(PAGE);
    // Le formulaire d'enrôlement et le bouton de révocation sont deux écritures.
    // Une seule des deux protégée serait une porte ouverte.
    expect(source).toContain("mayManage && !issued");
    expect(source).toContain('mayManage && connector.status !== "revoked"');
  });
});

describe("The one-time enrollment code", () => {
  test("it is held in component state, never in browser storage", () => {
    // Nova n'en garde que l'empreinte : un code rangé par la console serait la
    // seule copie durable en clair, et personne ne l'aurait décidé.
    const source = code(PAGE);
    expect(source).toContain("useState<ConnectorEnrollmentView | null>(null)");
    expect(source).not.toContain("localStorage");
    expect(source).not.toContain("sessionStorage");
  });

  test("the screen says it will not be shown again", () => {
    expect(code(PAGE)).toContain("enrollOnce");
    expect(code(PAGE)).toContain("expires_in");
  });

  test("the generated command carries no private address", () => {
    // L'adresse du plan de données appartient à l'organisation. Nova ne la
    // connaît pas, et la console n'a donc rien à en dire.
    const source = code(PAGE);
    expect(source).toContain("window.location.origin");
    expect(source).not.toContain("data_plane");
    expect(source).not.toContain("127.0.0.1");
  });
});

describe("Delivered is not applied", () => {
  test("the two revisions are shown separately", () => {
    const source = code(PAGE);
    expect(source).toContain("connector.delivered_revision");
    expect(source).toContain("connector.applied_revision");
  });

  test("the console never claims an update it cannot perform", () => {
    // Un protocole trop ancien se dit ; il ne se répare pas tout seul, et la
    // page ne promet aucune mise à jour automatique.
    const source = code(PAGE);
    expect(source).toContain("connector.protocol_supported");
    expect(source).toContain("updateRecommended");
    expect(source).not.toContain("autoUpdate");
  });
});

describe("Privacy boundary", () => {
  test("the page reads no user content and no credential", () => {
    const source = code(PAGE) + code(CONTROL);
    for (const forbidden of [
      "transcript",
      "prompt",
      "audio",
      "clipboard",
      "credential_hash",
      "connector_credential",
    ]) {
      expect(source.toLowerCase()).not.toContain(forbidden);
    }
  });

  test("the connector view type exposes no secret", () => {
    const view = code(CONTROL).slice(
      code(CONTROL).indexOf("export interface ConnectorView {"),
      code(CONTROL).indexOf("export interface ConnectorListView"),
    );
    expect(view).not.toContain("credential");
    expect(view).not.toContain("token");
  });
});

describe("Mounting", () => {
  test("the connector lives under Deployment, before the runtime", () => {
    const source = code(REST);
    expect(source).toContain("<PrivateConnector />");
    expect(source.indexOf("<PrivateConnector />")).toBeLessThan(
      source.indexOf("<AIRuntime />"),
    );
  });
});
