/**
 * Apprendre dans la console : l'éditeur de leçons, vu depuis ses garanties.
 *
 * Le serveur reste l'autorité — il refuse une leçon mal formée. Ces règles
 * existent côté console pour qu'un administrateur voie ce qui manque avant
 * d'enregistrer, et pour que la console ne propose jamais ce que le serveur
 * refusera : un exercice IA, des traductions dont les blocs divergent.
 */

import { describe, expect, test } from "bun:test";
import {
  EDITABLE_BLOCK_TYPES,
  alignTranslation,
  customLessonPayload,
  dueDateToInput,
  emptyBlock,
  emptyCustomLesson,
  inputToDueDate,
  lessonProblems,
  lessonsByModule,
  moveBlock,
  removeBlock,
  skeletonOf,
  type CustomLessonDraft,
  type LessonBlock,
} from "./learning";

function text(id: string, order: number, body = "Body"): LessonBlock {
  return { id, type: "text", order, content: { body } };
}

function quiz(id: string, order: number, labels = ["Yes", "No"]): LessonBlock {
  return {
    id,
    type: "multiple_choice",
    order,
    content: {
      question: "Which one?",
      options: [
        { id: "a", label: labels[0] },
        { id: "b", label: labels[1] },
      ],
      correct_option_id: "a",
      explanation: "Because.",
    },
  };
}

function validLesson(): CustomLessonDraft {
  return {
    module_id: "use-ai-foundations",
    source_locale: "en",
    estimated_minutes: 4,
    difficulty: "beginner",
    translations: {
      en: {
        title: "Our report rules",
        description: "What every report needs.",
        blocks: [text("intro", 1), quiz("check", 2)],
      },
    },
  };
}

describe("Block types", () => {
  test("the editor never offers an AI exercise", () => {
    // Son instruction ne peut venir que du catalogue livré : le serveur refuse
    // un exercice IA saisi en console, la console ne le propose donc pas.
    expect(EDITABLE_BLOCK_TYPES).not.toContain("ai_exercise");
    for (const type of ["text", "comparison", "multiple_choice", "takeaway"]) {
      expect(EDITABLE_BLOCK_TYPES).toContain(type);
    }
  });

  test("a new block gets an unused id and the next order", () => {
    const first = emptyBlock("text", []);
    const second = emptyBlock("text", [first]);
    expect(second.id).not.toBe(first.id);
    expect(first.order).toBe(1);
    expect(second.order).toBe(2);
  });

  test("a new quiz starts with two options and no answer chosen", () => {
    const block = emptyBlock("multiple_choice", []);
    const options = block.content.options as { id: string; label: string }[];
    expect(options).toHaveLength(2);
    expect(new Set(options.map((option) => option.id)).size).toBe(2);
    expect(block.content.correct_option_id).toBe("");
  });
});

describe("Ordering", () => {
  test("moving a block renumbers the orders from 1", () => {
    const moved = moveBlock([text("a", 1), text("b", 2), text("c", 3)], 2, 0);
    expect(moved.map((block) => block.id)).toEqual(["c", "a", "b"]);
    expect(moved.map((block) => block.order)).toEqual([1, 2, 3]);
  });

  test("an out-of-range move changes nothing", () => {
    const blocks = [text("a", 1), text("b", 2)];
    expect(moveBlock(blocks, 0, 5)).toEqual(blocks);
    expect(moveBlock(blocks, -1, 0)).toEqual(blocks);
  });

  test("removing a block keeps an unbroken sequence", () => {
    const left = removeBlock([text("a", 1), text("b", 2), text("c", 3)], 1);
    expect(left.map((block) => [block.id, block.order])).toEqual([
      ["a", 1],
      ["c", 2],
    ]);
  });
});

describe("Validation mirrors the server", () => {
  test("a complete lesson has no problem", () => {
    expect(lessonProblems(validLesson())).toEqual([]);
  });

  test("an empty lesson names what is missing", () => {
    const problems = lessonProblems(emptyCustomLesson("use-ai-foundations", "en"));
    expect(problems).toContainEqual({ locale: "en", blockId: null, field: "title" });
    expect(problems).toContainEqual({ locale: "en", blockId: null, field: "description" });
    expect(problems).toContainEqual({ locale: "en", blockId: null, field: "blocks" });
  });

  test("the duration stays between 1 and 10 minutes", () => {
    const lesson = { ...validLesson(), estimated_minutes: 45 };
    expect(lessonProblems(lesson)).toContainEqual({
      locale: null,
      blockId: null,
      field: "estimated_minutes",
    });
  });

  test("a quiz needs a correct answer and an explanation", () => {
    const lesson = validLesson();
    const check = lesson.translations.en.blocks[1];
    check.content = { ...check.content, correct_option_id: "", explanation: " " };
    const fields = lessonProblems(lesson)
      .filter((problem) => problem.blockId === "check")
      .map((problem) => problem.field);
    expect(fields).toContain("correct_option_id");
    expect(fields).toContain("explanation");
  });

  test("a comparison needs both sides", () => {
    const lesson = validLesson();
    lesson.translations.en.blocks = [
      { id: "cmp", type: "comparison", order: 1, content: { worse: "Vague", better: "" } },
    ];
    expect(lessonProblems(lesson)).toContainEqual({
      locale: "en",
      blockId: "cmp",
      field: "better",
    });
  });

  test("a translation whose blocks drift from the source is flagged", () => {
    const lesson = validLesson();
    lesson.translations.fr = {
      title: "Nos règles",
      description: "Ce que chaque rapport contient.",
      blocks: [text("intro", 1, "Bonjour")],
    };
    expect(lessonProblems(lesson)).toContainEqual({
      locale: "fr",
      blockId: null,
      field: "blocks_mismatch",
    });
  });
});

describe("Translations", () => {
  test("a translation follows the source blocks and keeps its own text", () => {
    const source = [text("intro", 1, "Hello"), quiz("check", 2)];
    const existing = [text("intro", 1, "Bonjour"), text("gone", 2, "Ancien")];
    const aligned = alignTranslation(source, existing);
    expect(skeletonOf(aligned)).toEqual(skeletonOf(source));
    expect(aligned[0].content.body).toBe("Bonjour");
    // Un bloc nouveau dans la source part de son texte : la leçon reste
    // valide, et l'administrateur voit ce qu'il reste à traduire.
    expect(aligned[1].content.question).toBe("Which one?");
  });

  test("translated option labels survive while the answer follows the source", () => {
    const source = quiz("check", 1);
    source.content = { ...source.content, correct_option_id: "b" };
    const existing = [quiz("check", 1, ["Oui", "Non"])];
    const [aligned] = alignTranslation([source], existing);
    const labels = (aligned.content.options as { label: string }[]).map((o) => o.label);
    expect(labels).toEqual(["Oui", "Non"]);
    expect(aligned.content.correct_option_id).toBe("b");
  });
});

describe("Payloads", () => {
  test("only the fields the server accepts are sent", () => {
    const payload = customLessonPayload({
      ...validLesson(),
      id: "org-1",
      revision: 3,
      version: 1,
    } as CustomLessonDraft);
    expect(Object.keys(payload).sort()).toEqual([
      "difficulty",
      "estimated_minutes",
      "module_id",
      "source_locale",
      "translations",
    ]);
  });
});

describe("Due dates", () => {
  test("a day entered by the administrator ends at the end of that local day", () => {
    const seconds = inputToDueDate("2027-06-30");
    expect(seconds).not.toBeNull();
    const date = new Date((seconds as number) * 1000);
    expect([date.getFullYear(), date.getMonth(), date.getDate()]).toEqual([2027, 5, 30]);
    expect([date.getHours(), date.getMinutes()]).toEqual([23, 59]);
    expect(dueDateToInput(seconds)).toBe("2027-06-30");
  });

  test("no date means no due date", () => {
    expect(inputToDueDate("")).toBeNull();
    expect(dueDateToInput(null)).toBe("");
  });
});

describe("Listing", () => {
  test("lessons are grouped under their module, catalogue lessons first", () => {
    const groups = lessonsByModule({
      modules: [
        { id: "m1", title: "Start", path_id: "p1", path_title: "Getting started", pillar: "use_ai" },
        { id: "m2", title: "Ask", path_id: "p2", path_title: "Asking well", pillar: "use_ai" },
      ],
      lessons: [
        { id: "org-x", module_id: "m2", custom: true },
        { id: "ask", module_id: "m2", custom: false },
        { id: "start", module_id: "m1", custom: false },
        { id: "lost", module_id: "gone", custom: true },
      ],
    });
    expect(groups.map((group) => [group.module.id, group.lessons.map((l) => l.id)])).toEqual([
      ["m1", ["start"]],
      ["m2", ["ask", "org-x"]],
    ]);
  });
});
