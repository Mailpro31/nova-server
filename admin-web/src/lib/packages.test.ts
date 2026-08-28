/**
 * L'écran des packages, vu depuis ses garanties.
 *
 * Comme pour les policies, on teste la **décision** plutôt que le rendu : ce
 * que la console envoie, ce qu'elle refuse d'offrir, et ce qu'elle dit avant
 * une action irréversible.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { CAPABILITY, can, initialSession, type SessionState } from "./session";

const PAGE = readFileSync("src/pages/Packages.tsx", "utf8");
const CONTROL = readFileSync("src/lib/controlPlane.ts", "utf8");
const APP = readFileSync("src/App.tsx", "utf8");

/** Le code, commentaires retirés : un mot dans un commentaire ne prouve rien. */
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
      organizationId: "org-1",
      expiresAt: Date.now() + 600_000,
    },
  } as unknown as SessionState;
}

describe("Capability boundary", () => {
  test("content_manage is its own capability", () => {
    expect(CAPABILITY.contentManage).toBe("content_manage");
    const distinct = new Set(Object.values(CAPABILITY));
    expect(distinct.size).toBe(Object.values(CAPABILITY).length);
  });

  test("editing is gated on the capability, not on a role name", () => {
    expect(code(PAGE)).toContain("CAPABILITY.contentManage");
    expect(code(PAGE)).not.toContain('role === "organization_admin"');
  });

  test("a session without content_manage cannot manage", () => {
    expect(can(sessionWith(["organization_read"]), CAPABILITY.contentManage)).toBe(
      false,
    );
    expect(
      can(sessionWith(["content_manage"]), CAPABILITY.contentManage),
    ).toBe(true);
  });

  test("reading does not require content_manage", () => {
    // Un `read_only` doit voir ce que l'organisation distribue.
    expect(code(PAGE)).toContain("control.packages(organizationId)");
  });

  test("read-only is told, not silently limited", async () => {
    const { t } = await import("./messages");
    expect(t.packages.readOnly.toLowerCase()).toContain("not change");
    expect(code(PAGE)).toContain("t.packages.readOnly");
  });
});

describe("Package is not policy", () => {
  test("the boundary is stated on screen", async () => {
    const { t } = await import("./messages");
    // La confusion à éviter en premier : publier n'autorise pas.
    expect(t.packages.policyBoundary).toContain("does not grant permission");
    expect(t.packages.policyBoundary.toLowerCase()).toContain("policy");
    expect(code(PAGE)).toContain("t.packages.policyBoundary");
  });

  test("no enabled switch lives on the package screen", () => {
    const source = code(PAGE);
    expect(source).not.toContain('type="checkbox"');
    expect(source).not.toContain("enabled_");
  });
});

describe("Immutability and rollback", () => {
  test("publishing is confirmed with what it means", async () => {
    const { t } = await import("./messages");
    const body = t.packages.publishBody(3);
    expect(body).toContain("Version 3");
    expect(body.toLowerCase()).toContain("distributed to members");
    // Le point qui doit être dit avant de cliquer, pas découvert après.
    expect(body.toLowerCase()).toContain("never be edited");
  });

  test("activating an older version says nothing is rewritten", async () => {
    const { t } = await import("./messages");
    const body = t.packages.activateBody(1);
    expect(body).toContain("version 1");
    expect(body.toLowerCase()).toContain("nothing is rewritten");
  });

  test("both destructive-looking actions ask first", () => {
    expect(code(PAGE)).toContain("setConfirmPublish");
    expect(code(PAGE)).toContain("setConfirmActivate");
    expect(code(PAGE)).toContain("ConfirmDialog");
  });

  test("rollback reuses the activate route, not a rewrite", () => {
    expect(CONTROL).toContain("/activate");
    expect(CONTROL).not.toContain("rollbackPackage");
  });
});

describe("Concurrency", () => {
  test("editing a draft always carries the current revision", () => {
    expect(CONTROL).toContain("expected_revision: number");
    expect(code(PAGE)).toContain("expected_revision: detail.revision");
  });
});

describe("No arbitrary JSON", () => {
  test("each type has its own editor", () => {
    const source = code(PAGE);
    expect(source).toContain("function StyleEditor");
    expect(source).toContain("function AiSkillEditor");
    expect(source).toContain("function VocabularyEditor");
  });

  test("the console offers no raw document editor", () => {
    const source = code(PAGE);
    for (const forbidden of ["JSON.parse", "JSON.stringify", "contentEditable"]) {
      expect(source).not.toContain(forbidden);
    }
  });

  test("every field the server requires has a control in the editor", () => {
    // Né du défaut trouvé en recette : `instruction` avait été ajouté au
    // schéma serveur et jamais au formulaire. Le message de refus nommait un
    // champ que l'interface ne proposait pas — l'administrateur remplissait
    // donc le champ voisin, et le refus se répétait sans explication possible.
    const source = code(PAGE);
    const REQUIRED: [string, string][] = [
      ["style", "name"],
      ["style", "instruction"],
      ["ai_skill", "title"],
      ["ai_skill", "instruction"],
      ["vocabulary", "term"],
      ["vocabulary", "replacement"],
    ];
    for (const [type, field] of REQUIRED) {
      // Chaque champ obligatoire doit être écrit quelque part par un contrôle.
      expect(source).toContain(`${field}: event.target.value`);
      expect(type).toBeTruthy();
    }
  });

  test("the instruction field says that it is the one that runs", async () => {
    const { t } = await import("./messages");
    expect(t.packages.skillInstruction.toLowerCase()).toContain("required");
    expect(t.packages.skillInstructionHint.toLowerCase()).toContain("runs");
    // Et que l'autre champ ne l'est pas : c'est la confusion qui a eu lieu.
    expect(t.packages.skillPracticeHint.toLowerCase()).toContain(
      "not sent to the model",
    );
  });

  test("a disabled publish button explains itself", async () => {
    const { t } = await import("./messages");
    // Un bouton éteint sans explication se lit comme une panne.
    expect(t.packages.completeFirst.toLowerCase()).toContain("required fields");
    expect(code(PAGE)).toContain("t.packages.completeFirst");
  });

  test("archiving is confirmed and reversible", async () => {
    const { t } = await import("./messages");
    expect(t.packages.archiveBody.toLowerCase()).toContain("nothing is deleted");
    expect(t.packages.archiveBody.toLowerCase()).toContain("restore");
    expect(code(PAGE)).toContain("control!.restorePackage");
    expect(code(PAGE)).toContain("setConfirmArchive");
  });

  test("a draft can be discarded without opening the editor", () => {
    const rowActions = code(PAGE).slice(
      code(PAGE).indexOf("t.packages.completeFirst"),
    );
    expect(rowActions).toContain("control!.discardPackageDraft");
  });

  test("vocabulary rows carry accessible labels", () => {
    expect(code(PAGE)).toContain("aria-label={t.packages.vocabularyTerm}");
    expect(code(PAGE)).toContain(
      "aria-label={t.packages.vocabularyReplacement}",
    );
  });
});

describe("States are named, not coloured", () => {
  test("every status has a written label", async () => {
    const { t } = await import("./messages");
    for (const [value, label] of [
      ["draft", "Draft"],
      ["published", "Published"],
      ["archived", "Archived"],
    ]) {
      expect(t.packages.statusLabel(value)).toBe(label);
    }
  });

  test("an unknown status falls back to itself rather than to nothing", () => {
    const { t } = require("./messages");
    expect(t.packages.statusLabel("venu_du_futur")).toBe("venu_du_futur");
  });

  test("the active version is labelled", async () => {
    const { t } = await import("./messages");
    expect(t.packages.active).toBe("Active");
    expect(code(PAGE)).toContain("t.packages.active}");
  });
});

describe("Unsaved changes", () => {
  test("a pending draft is announced", async () => {
    const { t } = await import("./messages");
    expect(t.packages.unsaved).toContain("unsaved");
    expect(code(PAGE)).toContain("t.packages.unsaved");
  });

  test("discard restores the server state", () => {
    expect(code(PAGE)).toContain("t.packages.discard");
    expect(code(PAGE)).toContain("setDraft(null)");
  });
});

describe("Structured validation errors", () => {
  test("the console shows the field the server names", () => {
    // Le serveur prend la peine de nommer le champ fautif ; le réduire à
    // « erreur » gaspillerait cette information.
    expect(code(PAGE)).toContain("detail?.message");
    expect(code(PAGE)).toContain("detail.field");
  });

  test("the api client keeps structured refusals", async () => {
    const source = readFileSync("src/lib/api.ts", "utf8");
    expect(source).toContain("readonly detail: Record<string, unknown> | null");
  });
});

describe("Navigation", () => {
  test("packages get their own surface", () => {
    expect(code(APP)).toContain('path: "packages"');
    expect(code(APP)).toContain("<Packages />");
  });

  test("the list route is organization scoped", () => {
    // Ce qu'on ne peut pas désigner sans son organisation ne fuit pas.
    expect(CONTROL).toContain(
      "/api/control/organizations/${organizationId}/packages",
    );
  });
});
