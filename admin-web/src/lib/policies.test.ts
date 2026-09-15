/**
 * L'écran des policies produit, vu depuis ses garanties.
 *
 * Comme pour la garde d'accès, on teste la **décision** plutôt que le rendu :
 * qui peut écrire, ce que la console envoie, ce qu'elle affiche après une
 * sauvegarde, et ce qu'elle refuse de faire — écraser le travail d'un collègue,
 * inventer une valeur, offrir un éditeur libre.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CAPABILITY, can, initialSession, type SessionState } from "./session";

const REST = readFileSync("src/pages/Rest.tsx", "utf8");
const CONTROL = readFileSync("src/lib/controlPlane.ts", "utf8");

/** Le code, commentaires retirés : chercher un mot dans un commentaire ne
 *  prouve rien sur ce que la console fait. */
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
  test("policy_manage exists and is its own capability", () => {
    expect(CAPABILITY.policyManage).toBe("policy_manage");
    const distinct = new Set(Object.values(CAPABILITY));
    expect(distinct.size).toBe(Object.values(CAPABILITY).length);
  });

  test("editing is gated on policy_manage, not on a role name", () => {
    // La règle de toute la console : l'interface suit les capacités annoncées
    // par le serveur. Lire un nom de rôle reviendrait à réimplémenter la
    // matrice d'autorisation dans le navigateur.
    expect(code(REST)).toContain("CAPABILITY.policyManage");
    expect(code(REST)).not.toContain('role === "organization_admin"');
  });

  test("a session without policy_manage cannot edit", () => {
    expect(can(sessionWith(["organization_read"]), CAPABILITY.policyManage)).toBe(
      false,
    );
    expect(
      can(sessionWith(["organization_read", "policy_manage"]), CAPABILITY.policyManage),
    ).toBe(true);
  });

  test("reading does not require policy_manage", () => {
    // Un `read_only` doit voir la configuration : c'est ce qu'on attend de lui.
    expect(code(REST)).toContain("control.policy()");
  });
});

describe("Concurrency", () => {
  test("the mutation always carries an expected revision", () => {
    expect(CONTROL).toContain("expected_revision: expectedRevision");
    // Pas de valeur par défaut : oublier la révision doit être impossible, pas
    // silencieusement toléré.
    expect(CONTROL).toContain("updatePolicy(expectedRevision: number");
  });

  test("the console sends the revision it displayed", () => {
    expect(code(REST)).toContain("control!.updatePolicy(view.revision, settings)");
  });

  test("a conflict is surfaced, never overwritten", async () => {
    const { t } = await import("./messages");
    expect(t.policies.conflict).toContain("changed while you were editing");
    expect(t.policies.conflict.toLowerCase()).toContain("reload");
    expect(code(REST)).toContain('raised.kind === "conflict"');
    // Aucune reprise automatique : ni nouvelle tentative avec la révision
    // fraîche, ni écrasement.
    expect(code(REST)).not.toContain("retryWithLatest");
  });

  test("the conflict message is mapped from the server code", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("conflict", "POLICY_REVISION_CONFLICT")).toBe(
      t.policies.conflict,
    );
  });
});

describe("Source of truth", () => {
  test("the screen re-reads the server after saving", () => {
    const save = code(REST).slice(code(REST).indexOf("async function save()"));
    expect(save).toContain("retry()");
  });

  test("no value is simulated while a request is in flight", () => {
    // L'écran affiche `draft ?? view.settings` : soit une modification voulue
    // par l'administrateur, soit ce que le serveur a renvoyé. Jamais une
    // valeur devinée.
    expect(code(REST)).toContain("const settings = draft ?? view.settings;");
  });

  test("the default state is announced rather than disguised", async () => {
    const { t } = await import("./messages");
    expect(t.policies.defaults).toContain("default");
    expect(code(REST)).toContain("!view.configured");
  });
});

describe("No arbitrary JSON", () => {
  test("the console offers typed controls, not a document editor", () => {
    const source = code(REST);
    expect(source).toContain('type="checkbox"');
    for (const forbidden of ["JSON.parse", "textarea", "contentEditable"]) {
      expect(source).not.toContain(forbidden);
    }
  });

  test("only keys declared in the table are written", () => {
    // Les cles ne viennent plus de JSX repete mais d'une table : l'ecran ne
    // peut ecrire que ce qu'elle declare, et ajouter une policy revient a
    // ajouter une ligne.
    const source = code(REST);
    expect(source).toContain("onChange={(event) => set(item.key, event.target.checked)}");
    for (const key of [
      "ai_skills_enabled",
      "organization_vocabulary_enabled",
      "voice_commands_enabled",
      "engineering_notes_enabled",
      "file_import_enabled",
      // Les categories de l'application.
      "learning_enabled",
      "styles_enabled",
      "generated_instructions_enabled",
      "past_dictations_enabled",
    ]) {
      expect(source).toContain(`key: "${key}"`);
    }
  });

  test("closing Styles says dictations are still reformulated", async () => {
    // La question qu'on se pose en fermant Styles : les dictees perdent-elles
    // leur mise en forme ? Non, et l'ecran le dit.
    const { t } = await import("./messages");
    expect(t.policies.stylesBody.toLowerCase()).toContain("default style");
  });

  test("closing Prompts or History says the content stays on the device", async () => {
    const { t } = await import("./messages");
    expect(t.policies.deviceCategoryNote.toLowerCase()).toContain("device");
    expect(code(REST)).toContain("t.policies.deviceCategoryNote");
  });

  test("the file import switch says the dictation stays available", async () => {
    // La question qu'un administrateur se pose en lisant « import audio ».
    // Ne pas y repondre a l'ecran, c'est laisser croire qu'il coupe la dictee.
    const { t } = await import("./messages");
    expect(t.policies.dictationSafe.toLowerCase()).toContain("dictation");
    expect(t.policies.dictationSafe.toLowerCase()).toContain("always");
    expect(code(REST)).toContain("t.policies.dictationSafe");
  });

  test("every switch carries an accessible label", () => {
    expect(code(REST)).toContain("aria-label={item.title}");
  });

  test("an unknown key rejected by the server is explained, not echoed", async () => {
    const { errorMessage } = await import("./messages");
    const message = errorMessage("invalid", "POLICY_KEY_UNKNOWN:ai_skils");
    // Le nom de champ interne n'aide pas un administrateur ; il ne doit pas
    // remonter tel quel dans l'interface.
    expect(message).not.toContain("ai_skils");
    expect(message).toContain("Update Nova Admin");
  });
});

describe("Unsaved changes", () => {
  test("the screen tells the administrator when something is pending", async () => {
    const { t } = await import("./messages");
    expect(t.policies.unsaved).toContain("unsaved");
    expect(code(REST)).toContain("t.policies.unsaved");
  });

  test("a dirty state is computed against the server document", () => {
    expect(code(REST)).toContain("draft[key] !== view.settings[key]");
  });

  test("discarding restores the server state", () => {
    expect(code(REST)).toContain("t.policies.discard");
    expect(code(REST)).toContain("function reload()");
  });
});

describe("Wording", () => {
  test("policies are named as product features, not as backend keys", async () => {
    const { t } = await import("./messages");
    expect(t.policies.aiSkillsTitle).toBe("AI Skills");
    expect(t.policies.aiSkillsBody).toBe(
      "Allow members to use AI Skills in Nova.",
    );
    for (const value of Object.values(t.policies)) {
      expect(value).not.toContain("_");
      expect(value).not.toContain("schema_version");
    }
  });

  test("the identity boundary is stated on screen", async () => {
    const { t } = await import("./messages");
    // Un administrateur doit pouvoir s'assurer qu'un interrupteur produit ne
    // peut pas lui fermer la porte, sans lire la documentation.
    const boundary = t.policies.boundary.toLowerCase();
    expect(boundary).toContain("sign-in");
    expect(boundary).toContain("administrator access");
    expect(boundary).toContain("audit");
    expect(code(REST)).toContain("t.policies.boundary");
  });

  test("no new sidebar entry was invented", () => {
    const { t } = require("./messages");
    // Les policies vivent dans Configuration : une entrée de menu de plus pour
    // un seul réglage encombrerait la navigation.
    expect(Object.values(t.nav)).not.toContain("Product policies");
    expect(code(REST)).toContain("<Policies />");
  });
});
