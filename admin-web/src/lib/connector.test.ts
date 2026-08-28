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

  test("the connector view carries credential metadata but no secret", () => {
    // La distinction est le sujet : l'age, la generation et l'etat de bascule
    // servent a administrer ; une empreinte ou un fragment de secret ne
    // serviraient qu'a exploiter une fuite.
    const view = code(CONTROL).slice(
      code(CONTROL).indexOf("export interface ConnectorView {"),
      code(CONTROL).indexOf("export interface ConnectorListView"),
    );
    expect(view).toContain("rotation_state");
    for (const secret of [
      "credential_hash",
      "connector_credential",
      "pending_credential",
      "token",
      "fingerprint",
    ]) {
      expect(view).not.toContain(secret);
    }
  });
});

describe("Credential rotation", () => {
  test("requesting rotation is gated on deployment_manage", () => {
    const source = code(PAGE);
    expect(source).toContain("mayManage &&");
    expect(source).toContain("requestConnectorRotation");
  });

  test("the console never receives or displays the new secret", () => {
    // Faire transiter un credential durable par un navigateur d'administration
    // en ferait la piece la plus exposee de la chaine. La console pose un
    // drapeau ; le Connector recoit le secret directement.
    const rotate = code(CONTROL).slice(
      code(CONTROL).indexOf("requestConnectorRotation"),
      code(CONTROL).indexOf("revokeConnector"),
    );
    expect(rotate).toContain("body: {}");
    expect(rotate).not.toContain("credential:");
    expect(code(PAGE)).not.toContain("connector_credential");
  });

  test("the screen shows state and age, never a secret or a fingerprint", () => {
    const source = code(PAGE);
    expect(source).toContain("connector.credential.age_days");
    expect(source).toContain("connector.credential.rotation_state");
    expect(source).not.toContain("credential_hash");
    expect(source).not.toContain("fingerprint");
  });

  test("requested is not shown as done", () => {
    // « Demande » et « fait » sont deux faits differents, comme livre et
    // applique. L'ecran re-lit le serveur plutot que de refleter le clic.
    const rotation = code(PAGE).slice(code(PAGE).indexOf("async function requestRotation"));
    expect(rotation).toContain("retry()");
    const source = code(PAGE);
    expect(source).toContain("rotationLabel");
    expect(source).toContain('state === "pending"');
  });

  test("an in-flight rotation cannot be requested again from the screen", () => {
    const source = code(PAGE);
    expect(source).toContain('connector.credential.rotation_state === "requested"');
    expect(source).toContain('connector.credential.rotation_state === "pending"');
  });

  test("a connector that cannot rotate is told so instead of offered a button", () => {
    const source = code(PAGE);
    expect(source).toContain("connector.credential.rotation_supported");
    expect(source).toContain("rotationUnsupported");
  });

  test("rotation and revocation are presented as different answers", () => {
    // Un credential compromis se revoque ; la rotation suppose que l'ancien
    // fonctionne encore assez longtemps pour poser le nouveau.
    expect(code(PAGE)).toContain("rotationCompromised");
  });
});

describe("Version and updates", () => {
  test("the update surface follows the declared capability, not the OS", () => {
    // Un meme Linux peut porter les deux mecanismes. Deduire de l'OS
    // proposerait un jour un bouton a un conteneur.
    const source = code(PAGE);
    expect(source).toContain('connector.platform.update_mechanism === "self"');
    expect(source).toContain('connector.platform.update_mechanism === "external"');
    expect(source).not.toContain('platform.os === "linux"');
  });

  test("an orchestrator-managed connector gets a status, not an excuse", () => {
    // « Deployment-managed update required » est un etat affiche, avec la
    // marche a suivre — pas un silence.
    const source = code(PAGE);
    expect(source).toContain("externalRequired");
    expect(source).toContain("externalBody");
  });

  test("a connector predating update support is not assumed capable", () => {
    expect(code(PAGE)).toContain("selfUpdateUnknown");
  });

  test("a deployment that cannot update itself is told what to do instead", () => {
    // Un conteneur ne remplace pas sa propre image. Offrir le bouton quand
    // meme produirait un controle qui echoue a chaque fois ; la page explique
    // la marche a suivre a la place.
    const source = code(PAGE);
    expect(source).toContain('connector.platform.update_mechanism === "external"');
    expect(source).toContain("selfUpdateBody");
  });

  test("the update button is gated on capability, role and state", () => {
    const source = code(PAGE);
    expect(source).toContain("mayManage &&");
    expect(source).toContain('connector.platform.update_mechanism === "self" &&');
    expect(source).toContain('connector.update.support_status !== "up_to_date"');
    // Une demande deja en cours ne se redemande pas.
    expect(source).toContain("connector.update.target_version !== null");
  });

  test("the request carries a version and nothing executable", () => {
    const update = code(CONTROL).slice(
      code(CONTROL).indexOf("requestConnectorUpdate"),
      code(CONTROL).indexOf("revokeConnector"),
    );
    expect(update).toContain("target_version: targetVersion");
    for (const forbidden of ["command", "shell", "script", "args", "exec", "url"]) {
      expect(update.toLowerCase()).not.toContain(forbidden);
    }
  });

  test("requested is not shown as installed", () => {
    // « Demande » et « installe » sont deux faits differents, comme livre et
    // applique. L'ecran re-lit le serveur plutot que de refleter le clic.
    const update = code(PAGE).slice(code(PAGE).indexOf("async function requestUpdate"));
    expect(update).toContain("retry()");
    expect(code(PAGE)).toContain("connector.update.state");
  });

  test("an unsupported connector is flagged, not presented as disconnected", () => {
    const source = code(PAGE);
    expect(source).toContain('connector.update.support_status === "unsupported"');
    expect(source).toContain("unsupportedBody");
  });

  test("the screen shows the version chain without inventing it", () => {
    const source = code(PAGE);
    for (const field of [
      "connector.connector_version",
      "connector.update.recommended_version",
      "connector.update.minimum_supported_version",
      "connector.update.last_known_good_version",
      "connector.update.last_error_code",
    ]) {
      expect(source).toContain(field);
    }
  });

  test("no release manifest, signature or artifact url reaches the console", () => {
    // La console administre ; elle n'est pas un maillon de la chaine
    // d'approvisionnement, et n'a donc rien a porter de tout cela.
    const source = code(PAGE) + code(CONTROL);
    for (const forbidden of ["signature", "manifest", "digest", "artifact"]) {
      expect(source.toLowerCase()).not.toContain(forbidden);
    }
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
