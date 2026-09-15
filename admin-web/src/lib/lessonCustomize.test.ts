/**
 * Personnaliser une leçon livrée, et décider des mises à jour de Nova.
 *
 * Le serveur copie une leçon livrée dans une leçon de l'organisation, qui
 * remplace l'original pour les membres. Quand Nova livre ensuite une version
 * plus récente, rien ne change d'office : la console la signale, la montre, et
 * l'administrateur l'applique ou garde sa version.
 *
 * On teste ce que la console envoie, ce qu'elle dit avant d'agir, et qu'elle
 * ne transmet jamais l'origine d'une copie depuis l'éditeur.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  canCustomize,
  customLessonPayload,
  proposalTranslation,
  type CustomLessonView,
  type LearningLessonView,
  type NovaUpdateProposal,
} from "./learning";

const PAGE = readFileSync("src/pages/Learn.tsx", "utf8");
const CONTROL = readFileSync("src/lib/controlPlane.ts", "utf8");

/** Le code, commentaires retirés : un mot dans un commentaire ne prouve rien. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

function listed(
  overrides: Partial<LearningLessonView> = {},
): LearningLessonView {
  return {
    id: "ask-better-questions",
    title: "Ask better questions",
    path_id: "asking-well",
    module_id: "use-ai-foundations",
    pillar: "use_ai",
    custom: false,
    based_on_lesson_id: null,
    update_available: false,
    status: "active",
    required: false,
    due_at: null,
    group_ids: [],
    revision: 0,
    updated_at: null,
    updated_by_user_id: null,
    ...overrides,
  };
}

const blocks = [
  { id: "intro", type: "text", order: 1, content: { body: "Hello." } },
];

describe("Model", () => {
  test("only a delivered lesson can be customized", () => {
    expect(canCustomize(listed())).toBe(true);
    expect(canCustomize(listed({ custom: true }))).toBe(false);
    expect(
      canCustomize(
        listed({ custom: true, based_on_lesson_id: "ask-better-questions" }),
      ),
    ).toBe(false);
  });

  test("the editor never sends the origin of a copy", () => {
    const view: CustomLessonView = {
      id: "org-1",
      module_id: "use-ai-foundations",
      source_locale: "en",
      estimated_minutes: 4,
      difficulty: "beginner",
      translations: { en: { title: "Copy", description: "A copy.", blocks } },
      version: 1,
      revision: 2,
      created_at: 0,
      updated_at: 0,
      updated_by_user_id: null,
      based_on: {
        lesson_id: "ask-better-questions",
        version: 1,
        latest_version: 2,
        update_available: true,
      },
    };
    expect(Object.keys(customLessonPayload(view)).sort()).toEqual([
      "difficulty",
      "estimated_minutes",
      "module_id",
      "source_locale",
      "translations",
    ]);
  });

  test("Nova's proposal is previewed like any lesson", () => {
    const proposal: NovaUpdateProposal = {
      lesson_id: "ask-better-questions",
      version: 2,
      current_version: 1,
      locale: "en",
      title: "Ask better questions",
      description: "New wording.",
      blocks,
    };
    expect(proposalTranslation(proposal)).toEqual({
      title: "Ask better questions",
      description: "New wording.",
      blocks,
    });
  });
});

describe("Client", () => {
  test("the customization routes are declared here", () => {
    expect(CONTROL).toContain("/customize");
    expect(CONTROL).toContain("/nova-update");
  });

  test("a decision carries the revision it was built on, and nothing else is possible", () => {
    expect(CONTROL).toMatch(
      /decideNovaUpdate\(\s*lessonId: string,\s*expectedRevision: number/,
    );
    expect(CONTROL).toContain('decision: "apply" | "dismiss"');
  });
});

describe("Page", () => {
  test("customizing goes through a confirmation that says what happens", async () => {
    const { t } = await import("./messages");
    expect(code(PAGE)).toContain("t.learn.customizeTitle");
    expect(code(PAGE)).toContain("control!.customizeLearningLesson(lesson.id)");
    expect(code(PAGE)).toContain("canCustomize(lesson)");
    const body = t.learn.customizeBody.toLowerCase();
    expect(body).toContain("copy");
    expect(body).toContain("archived");
    expect(body).toContain("progress");
  });

  test("the list says which lessons are customized and which have a Nova update", () => {
    expect(code(PAGE)).toContain("lesson.based_on_lesson_id");
    expect(code(PAGE)).toContain("lesson.update_available");
    expect(code(PAGE)).toContain("t.learn.updateAvailable");
  });

  test("Nova's version can be read before deciding", () => {
    expect(code(PAGE)).toContain("control.novaUpdate(lesson.id)");
    expect(code(PAGE)).toContain("proposalTranslation(");
  });

  test("applying Nova's version goes through a danger confirmation", async () => {
    const { t } = await import("./messages");
    expect(code(PAGE)).toContain("t.learn.applyTitle");
    expect(code(PAGE)).toContain(
      'decideNovaUpdate(lesson.id, lesson.revision, "apply")',
    );
    const body = t.learn.applyBody.toLowerCase();
    expect(body).toContain("replaced");
    expect(body).toContain("every language");
  });

  test("keeping our version is one click and says the proposal goes away", async () => {
    const { t } = await import("./messages");
    expect(code(PAGE)).toContain(
      'decideNovaUpdate(lesson.id, lesson.revision, "dismiss")',
    );
    expect(t.learn.keepHint.toLowerCase()).toContain("newer");
  });
});

describe("Messages", () => {
  test("the new server codes become readable sentences", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("conflict", "LEARNING_LESSON_NOT_CUSTOMIZABLE")).toBe(
      t.learn.notCustomizable,
    );
    expect(errorMessage("conflict", "LEARNING_UPDATE_NOT_AVAILABLE")).toBe(
      t.learn.updateNotAvailable,
    );
  });
});
