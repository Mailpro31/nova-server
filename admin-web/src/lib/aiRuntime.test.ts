/**
 * L'écran Deployment → AI Runtime, vu depuis ses garanties.
 *
 * Comme pour les policies, on teste la **décision** plutôt que le rendu : qui
 * peut écrire, ce que la console envoie, ce qu'elle refuse d'afficher, et ce
 * qu'elle refuse de prétendre. Une capture de rendu casserait au premier
 * changement de libellé sans rien dire de ces propriétés.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CAPABILITY, can, initialSession, type SessionState } from "./session";

const PAGE = readFileSync("src/pages/AIRuntime.tsx", "utf8");
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
      role: "organization_admin",
      expiresAt: Date.now() + 600_000,
    },
  } as unknown as SessionState;
}

describe("Capability boundary", () => {
  test("reconfiguring the runtime is gated on deployment_manage", () => {
    // L'interface suit les capacités annoncées par le serveur. Lire un nom de
    // rôle reviendrait à réimplémenter la matrice d'autorisation ici.
    expect(code(PAGE)).toContain("CAPABILITY.deploymentManage");
    expect(code(PAGE)).not.toContain('role === "organization_admin"');
  });

  test("a session without deployment_manage cannot change anything", () => {
    const readOnly = sessionWith(["organization_read", "diagnostics_read"]);
    expect(can(readOnly, CAPABILITY.deploymentManage)).toBe(false);
    expect(
      can(sessionWith(["deployment_manage"]), CAPABILITY.deploymentManage),
    ).toBe(true);
  });

  test("every control is disabled without the capability", () => {
    // Le champ désactivé plutôt que caché : un administrateur en lecture doit
    // voir la configuration qu'il ne peut pas changer, sinon il ne peut pas
    // en discuter avec la personne qui le peut.
    const controls = code(PAGE).match(/disabled=\{[^}]*\}/g) ?? [];
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      expect(control.includes("mayManage") || control.includes("busy")).toBe(true);
    }
  });
});

describe("Desired and applied never merge", () => {
  test("the applied state comes from what the runtime reported", () => {
    // Jamais de `desired.revision` recopié en « appliqué » : c'est le seul
    // moyen d'afficher « enregistré, pas encore appliqué » honnêtement.
    expect(code(PAGE)).toContain("view.reported.applied_revision");
    expect(code(PAGE)).toContain("view.desired.revision");
  });

  test("nothing is marked applied unless the revisions actually match", () => {
    const source = code(PAGE);
    expect(source).toContain(
      "view.reported.applied_revision === view.desired.revision",
    );
    // Une configuration jamais écrite n'est pas une configuration appliquée.
    expect(source).toContain("view.desired.revision > 0");
  });

  test("the console never claims a remote apply it cannot perform", () => {
    // La frontière de la Phase 33 est dans l'écran, pas seulement dans un
    // document : sans elle, une DSI croirait avoir reconfiguré un serveur
    // distant que ce plan de contrôle ne joint pas encore.
    expect(code(PAGE)).toContain("view.remote_apply.supported");
    expect(code(PAGE)).toContain("requires_private_connector");
  });
});

describe("Optimistic concurrency", () => {
  test("the update carries the revision the screen was built on", () => {
    expect(code(PAGE)).toContain("control!.updateAIRuntime(view.desired.revision");
    expect(code(CONTROL)).toContain("expected_revision: expectedRevision");
  });

  test("the screen re-reads instead of echoing what it sent", () => {
    // Une console qui s'affiche à elle-même son propre souhait finit par
    // montrer un état que le runtime ne porte pas.
    const save = code(PAGE).slice(code(PAGE).indexOf("async function save()"));
    expect(save).toContain("retry()");
    expect(save).toContain("setDraft(null)");
  });
});

describe("Privacy boundary", () => {
  test("the page reads no user content", () => {
    const source = code(PAGE) + code(CONTROL);
    for (const forbidden of [
      "transcript",
      "prompt",
      "last_input",
      "last_output",
      "dictation_text",
      "instruction",
    ]) {
      expect(source.toLowerCase()).not.toContain(forbidden);
    }
  });

  test("cloud fallback is shown as disabled and cannot be turned on here", () => {
    expect(code(PAGE)).toContain("cloudFallbackDisabled");
    // Le fallback se lit, il ne s'ecrit pas. La distinction est le sujet : un
    // champ lisible informe, un champ ecrivable serait le seul moyen d'envoyer
    // silencieusement du contenu chez un fournisseur externe.
    const update = code(CONTROL).slice(
      code(CONTROL).indexOf("updateAIRuntime("),
      code(CONTROL).indexOf("packages(organizationId: string)"),
    );
    expect(update).not.toContain("cloud_fallback");
    expect(code(PAGE)).not.toContain("cloud_fallback:");
  });
});

describe("Where the text goes", () => {
  test("the page says what the server reports, not a fixed promise", () => {
    // n° 21 bis : l'écran affirmait que rien ne sortait de l'école alors que
    // le serveur passait par une API en ligne.
    expect(code(PAGE)).toContain(
      'view.processing_location === "external_ai_provider"',
    );
  });
});

describe("Model agnosticism", () => {
  test("no commercial model name is written into the console", () => {
    // Le mapping profil → modèle appartient à la configuration du runtime. Un
    // nom écrit ici ferait de l'interface une seconde source de vérité.
    const source = (code(PAGE) + code(CONTROL) + code(REST)).toLowerCase();
    for (const vendor of [
      "qwen",
      "llama",
      "mistral",
      "openai",
      "anthropic",
      "gemini",
      "whisper-large",
    ]) {
      expect(source).not.toContain(vendor);
    }
  });

  test("the console knows the four profile names", () => {
    const labels = [...code(PAGE).matchAll(/^\s{2}(\w+): t\.aiRuntime\./gm)].map(
      (match) => match[1],
    );
    expect(labels).toEqual(["efficient", "standard", "advanced", "custom"]);
  });

  test("the offered profiles come from the server, not from the console", () => {
    // Proposer un choix que l'enregistrement refuse n'est pas proposer un
    // choix. `custom` n'existe que si le deploiement a declare ses parametres.
    expect(code(PAGE)).toContain("view.available_profiles.map");
    expect(code(PAGE)).not.toContain("PROFILES.map");
  });

  test("an unavailable custom profile is explained, not silently missing", () => {
    expect(code(PAGE)).toContain('view.available_profiles.includes("custom")');
    expect(code(PAGE)).toContain("customUnavailable");
  });

  test("the custom parameters are displayed but never sent back", () => {
    // Ils viennent de la configuration du deploiement. Les rendre ecrivables
    // ici ouvrirait depuis un navigateur la surface gardee cote exploitation.
    expect(code(PAGE)).toContain("view.custom_profile.available");
    expect(code(PAGE)).toContain("view.custom_profile.max_output_tokens");
    const update = code(CONTROL).slice(
      code(CONTROL).indexOf("updateAIRuntime("),
      code(CONTROL).indexOf("packages(organizationId: string)"),
    );
    for (const forbidden of [
      "max_output_tokens",
      "temperature",
      "custom_profile",
      "text_model",
      "base_url",
    ]) {
      expect(update).not.toContain(forbidden);
    }
  });

  test("the recommended profile is shown but never applied on its own", () => {
    expect(code(PAGE)).toContain("view.recommended_profile");
    // Aucune écriture déclenchée par la recommandation : choisir le profil
    // engage la latence de tout le parc, c'est une décision d'administrateur.
    expect(code(PAGE)).not.toContain("profile: view.recommended_profile");
  });
});

describe("Mounting", () => {
  test("the runtime lives under Deployment", () => {
    expect(code(REST)).toContain("<AIRuntime />");
  });
});
