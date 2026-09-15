/**
 * La page Apprendre de la console, vue depuis ses garanties.
 *
 * Comme pour les policies et les packages, on teste la **décision** : ce que la
 * console envoie, qui peut écrire, ce qu'elle dit avant une action
 * irréversible, et ce qu'elle refuse d'offrir.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const PAGE = readFileSync("src/pages/Learn.tsx", "utf8");
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

describe("Navigation", () => {
  test("Learn has its own page", async () => {
    const { t } = await import("./messages");
    expect(t.nav.learn).toBe("Learn");
    expect(code(APP)).toContain('path: "learn"');
    expect(code(APP)).toContain("<Learn />");
  });
});

describe("Client", () => {
  test("every learning route the page needs is declared once, here", () => {
    for (const route of [
      "/api/control/organization/learning",
      "/api/control/organization/learning/custom-lessons",
      "/archive",
      "/restore",
    ]) {
      expect(CONTROL).toContain(route);
    }
  });

  test("every mutation carries the revision it was built on", () => {
    // Sans valeur par défaut : oublier la révision doit être impossible.
    expect(CONTROL).toMatch(/updateLearningLesson\(\s*lessonId: string,\s*expectedRevision: number/);
    expect(CONTROL).toMatch(/updateCustomLesson\(\s*lessonId: string,\s*expectedRevision: number/);
    expect(CONTROL).toContain("expected_revision=${expectedRevision}");
  });
});

describe("Capability boundary", () => {
  test("editing is gated on content_manage, not on a role name", () => {
    expect(code(PAGE)).toContain("CAPABILITY.contentManage");
    expect(code(PAGE)).not.toContain('role === "organization_admin"');
  });
});

describe("Editor", () => {
  test("the page never offers an AI exercise", () => {
    expect(code(PAGE)).not.toContain("ai_exercise");
    expect(code(PAGE)).toContain("EDITABLE_BLOCK_TYPES");
  });

  test("problems are shown before saving, and block the save", () => {
    expect(code(PAGE)).toContain("lessonProblems(");
    expect(code(PAGE)).toContain("problems.length > 0");
  });

  test("blocks move by drag and drop and by keyboard", () => {
    expect(code(PAGE)).toContain("draggable");
    expect(code(PAGE)).toContain("onDrop");
    expect(code(PAGE)).toContain("t.learn.moveUp");
    expect(code(PAGE)).toContain("t.learn.moveDown");
  });

  test("a translation is aligned on the source blocks", () => {
    expect(code(PAGE)).toContain("alignTranslation(");
  });

  test("the lesson is previewed as members will read it", () => {
    expect(code(PAGE)).toContain("<LessonPreview");
  });
});

describe("Irreversible actions", () => {
  test("deleting goes through a danger confirmation", async () => {
    const { t } = await import("./messages");
    expect(code(PAGE)).toContain("t.learn.deleteTitle");
    expect(code(PAGE)).toContain('tone="danger"');
    expect(t.learn.deleteBody.toLowerCase()).toContain("progress");
    expect(t.learn.deleteBody.toLowerCase()).toContain("cannot be undone");
  });

  test("archiving says nothing is lost", async () => {
    const { t } = await import("./messages");
    expect(t.learn.archiveBody.toLowerCase()).toContain("progress is kept");
  });

  test("a conflict is surfaced, never overwritten", () => {
    expect(code(PAGE)).toContain('raised.kind === "conflict"');
  });
});

describe("Messages", () => {
  test("server codes become readable sentences", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("conflict", "LEARNING_REVISION_CONFLICT")).toBe(t.learn.conflict);
    expect(errorMessage("invalid", "LEARNING_CUSTOM_LESSON_INVALID")).toBe(t.learn.invalid);
    expect(errorMessage("conflict", "LEARNING_CUSTOM_LESSON_LIMIT")).toBe(t.learn.limit);
    expect(errorMessage("not_found", "LESSON_NOT_FOUND")).toBe(t.learn.notFound);
  });
});
