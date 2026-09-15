/**
 * Apprendre dans la console : le modèle de l'éditeur de leçons.
 *
 * ## Le serveur décide, la console prévient
 *
 * Le serveur refuse une leçon mal formée, et reste l'autorité. Les règles
 * reprises ici servent à montrer ce qui manque **avant** d'enregistrer : un
 * refus sans explication après avoir rempli dix blocs ne dit pas lequel est en
 * défaut.
 *
 * ## Les blocs se décident dans la langue source
 *
 * La progression d'un membre retient des identifiants de blocs, et une réponse
 * juste un identifiant d'option. Toutes les langues partagent donc les mêmes
 * blocs : on les ajoute, déplace ou supprime dans la langue source, et une
 * traduction ne fait que traduire leurs textes — voir `alignTranslation`.
 */

export interface LessonBlock {
  id: string;
  type: string;
  order: number;
  content: Record<string, unknown>;
}

export interface LessonTranslation {
  title: string;
  description: string;
  blocks: LessonBlock[];
}

/** Ce que la console envoie pour créer ou modifier une leçon. */
export interface CustomLessonDraft {
  module_id: string;
  source_locale: string;
  estimated_minutes: number;
  difficulty: string;
  translations: Record<string, LessonTranslation>;
}

/** D'où vient une copie personnalisée, et où en est Nova. */
export interface LessonOrigin {
  lesson_id: string;
  /** La version livrée copiée, ou la dernière que l'organisation a vue. */
  version: number;
  latest_version: number;
  update_available: boolean;
}

export interface CustomLessonView extends CustomLessonDraft {
  id: string;
  version: number;
  revision: number;
  created_at: number;
  updated_at: number;
  updated_by_user_id: string | null;
  /** `null` pour une leçon écrite depuis zéro. */
  based_on: LessonOrigin | null;
}

/** La version plus récente d'une leçon livrée, telle que Nova la propose. */
export interface NovaUpdateProposal {
  lesson_id: string;
  version: number;
  current_version: number;
  locale: string;
  title: string;
  description: string;
  blocks: LessonBlock[];
}

export interface LearningModuleView {
  id: string;
  title: string;
  path_id: string;
  path_title: string;
  pillar: string;
}

export interface LearningLessonView {
  id: string;
  title: string;
  path_id: string;
  module_id: string;
  pillar: string;
  custom: boolean;
  /** La leçon livrée d'une copie personnalisée, `null` sinon. */
  based_on_lesson_id: string | null;
  /** Nova a livré une version plus récente de l'original. */
  update_available: boolean;
  status: "active" | "archived";
  required: boolean;
  due_at: number | null;
  group_ids: string[];
  /** Révision des réglages (archivage, obligation, groupes). */
  revision: number;
  updated_at: number | null;
  updated_by_user_id: string | null;
}

export interface LearningAdminView {
  lessons: LearningLessonView[];
  groups: { id: string; label: string }[];
  modules: LearningModuleView[];
  locales: string[];
}

export interface LessonProblem {
  /** `null` pour un réglage de la leçon, commun à toutes les langues. */
  locale: string | null;
  blockId: string | null;
  field: string;
}

interface Option {
  id: string;
  label: string;
}

/**
 * Les blocs qu'un administrateur peut créer.
 *
 * `ai_exercise` en est absent à dessein : son instruction ne vient que du
 * catalogue livré, et le serveur refuse qu'une console en écrive une.
 */
export const EDITABLE_BLOCK_TYPES: readonly string[] = [
  "text",
  "example",
  "comparison",
  "tip",
  "warning",
  "multiple_choice",
  "reflection",
  "prompt_exercise",
  "takeaway",
];

export const DIFFICULTIES = ["beginner", "intermediate", "advanced"] as const;
export const MIN_MINUTES = 1;
export const MAX_MINUTES = 10;
export const MAX_BLOCKS = 40;
export const MAX_TITLE = 120;
export const MAX_DESCRIPTION = 400;

/** Les textes de chaque type de bloc, dans l'ordre où l'éditeur les montre. */
export const BLOCK_TEXT_FIELDS: Record<string, readonly string[]> = {
  text: ["heading", "body"],
  example: ["heading", "body"],
  comparison: ["worse_label", "worse", "better_label", "better", "note"],
  tip: ["body"],
  warning: ["body"],
  multiple_choice: ["question", "explanation"],
  question: ["question", "explanation"],
  reflection: ["prompt"],
  prompt_exercise: ["instruction", "starting_text", "hint", "sample_answer"],
  takeaway: ["body"],
};

/** Ceux sans lesquels le serveur refuse le bloc. */
export const BLOCK_REQUIRED_FIELDS: Record<string, readonly string[]> = {
  text: ["body"],
  example: ["body"],
  comparison: ["worse", "better"],
  tip: ["body"],
  warning: ["body"],
  multiple_choice: ["question", "explanation"],
  question: ["question", "explanation"],
  reflection: ["prompt"],
  prompt_exercise: ["instruction", "sample_answer"],
  takeaway: ["body"],
};

function isQuiz(type: string): boolean {
  return type === "multiple_choice" || type === "question";
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function filled(value: unknown): boolean {
  return typeof value === "string" && value.trim() !== "";
}

function optionsOf(block: LessonBlock): Option[] {
  const options = block.content.options;
  return Array.isArray(options) ? (options as Option[]) : [];
}

export function emptyBlock(type: string, existing: LessonBlock[]): LessonBlock {
  const used = new Set(existing.map((block) => block.id));
  let index = existing.length + 1;
  while (used.has(`${type}-${index}`)) index += 1;
  const content: Record<string, unknown> = Object.fromEntries(
    (BLOCK_TEXT_FIELDS[type] ?? []).map((field) => [field, ""]),
  );
  if (isQuiz(type)) {
    content.options = [
      { id: "a", label: "" },
      { id: "b", label: "" },
    ];
    // Aucune bonne réponse par défaut : en choisir une à la place de
    // l'administrateur produirait un quiz juste en apparence.
    content.correct_option_id = "";
  }
  return { id: `${type}-${index}`, type, order: existing.length + 1, content };
}

export function emptyCustomLesson(moduleId: string, sourceLocale: string): CustomLessonDraft {
  return {
    module_id: moduleId,
    source_locale: sourceLocale,
    estimated_minutes: 5,
    difficulty: "beginner",
    translations: { [sourceLocale]: { title: "", description: "", blocks: [] } },
  };
}

function renumber(blocks: LessonBlock[]): LessonBlock[] {
  return blocks.map((block, index) => ({ ...block, order: index + 1 }));
}

export function moveBlock(blocks: LessonBlock[], from: number, to: number): LessonBlock[] {
  const inRange = (index: number) => index >= 0 && index < blocks.length;
  if (!inRange(from) || !inRange(to) || from === to) return blocks;
  const next = [...blocks];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return renumber(next);
}

export function removeBlock(blocks: LessonBlock[], index: number): LessonBlock[] {
  return renumber(blocks.filter((_, position) => position !== index));
}

/**
 * Ce que toutes les langues doivent partager — la même définition que le
 * serveur : identifiants, types, ordre, options et bonne réponse.
 */
export function skeletonOf(blocks: LessonBlock[]): unknown[] {
  return [...blocks]
    .sort((left, right) => left.order - right.order)
    .map((block) => [
      block.id,
      block.type,
      block.order,
      isQuiz(block.type) ? optionsOf(block).map((option) => option.id) : [],
      (block.content.correct_option_id as string | undefined) ?? null,
    ]);
}

/**
 * Une traduction recalée sur les blocs de la langue source.
 *
 * Les blocs viennent de la source ; les textes déjà traduits d'un bloc de
 * même identifiant et de même type sont gardés, libellés d'options compris.
 * Un bloc absent de la traduction part du texte source : la leçon reste
 * enregistrable, et il reste visible ce qui n'est pas encore traduit.
 */
export function alignTranslation(
  source: LessonBlock[],
  existing: LessonBlock[],
): LessonBlock[] {
  const previousById = new Map(existing.map((block) => [block.id, block]));
  return [...source]
    .sort((left, right) => left.order - right.order)
    .map((sourceBlock) => {
      const block = clone(sourceBlock);
      const previous = previousById.get(block.id);
      if (!previous || previous.type !== block.type) return block;
      for (const field of BLOCK_TEXT_FIELDS[block.type] ?? []) {
        if (filled(previous.content[field])) block.content[field] = previous.content[field];
      }
      if (isQuiz(block.type)) {
        const labels = new Map(optionsOf(previous).map((option) => [option.id, option.label]));
        block.content.options = optionsOf(block).map((option) => ({
          id: option.id,
          label: filled(labels.get(option.id)) ? (labels.get(option.id) as string) : option.label,
        }));
      }
      return block;
    });
}

function blockProblems(locale: string, block: LessonBlock): LessonProblem[] {
  const problems: LessonProblem[] = [];
  const at = (field: string) => problems.push({ locale, blockId: block.id, field });
  for (const field of BLOCK_REQUIRED_FIELDS[block.type] ?? []) {
    if (!filled(block.content[field])) at(field);
  }
  if (isQuiz(block.type)) {
    const options = optionsOf(block);
    if (options.length < 2 || options.some((option) => !filled(option.label))) at("options");
    if (!options.some((option) => option.id === block.content.correct_option_id)) {
      at("correct_option_id");
    }
  }
  return problems;
}

/** Tout ce qui ferait refuser la leçon par le serveur, nommé. Vide si rien. */
export function lessonProblems(draft: CustomLessonDraft): LessonProblem[] {
  const problems: LessonProblem[] = [];
  const minutes = draft.estimated_minutes;
  if (!Number.isInteger(minutes) || minutes < MIN_MINUTES || minutes > MAX_MINUTES) {
    problems.push({ locale: null, blockId: null, field: "estimated_minutes" });
  }
  if (!(DIFFICULTIES as readonly string[]).includes(draft.difficulty)) {
    problems.push({ locale: null, blockId: null, field: "difficulty" });
  }
  if (!draft.module_id) problems.push({ locale: null, blockId: null, field: "module_id" });

  const source = draft.translations[draft.source_locale];
  if (!source) {
    problems.push({ locale: draft.source_locale, blockId: null, field: "missing_source" });
  }
  const sourceShape = source ? JSON.stringify(skeletonOf(source.blocks)) : null;
  const locales = Object.keys(draft.translations).sort((left, right) =>
    left === draft.source_locale ? -1 : right === draft.source_locale ? 1 : left.localeCompare(right),
  );
  for (const locale of locales) {
    const translation = draft.translations[locale];
    const lessonField = (field: string) => problems.push({ locale, blockId: null, field });
    if (!filled(translation.title) || translation.title.length > MAX_TITLE) lessonField("title");
    if (!filled(translation.description) || translation.description.length > MAX_DESCRIPTION) {
      lessonField("description");
    }
    if (translation.blocks.length === 0 || translation.blocks.length > MAX_BLOCKS) {
      lessonField("blocks");
    }
    for (const block of translation.blocks) problems.push(...blockProblems(locale, block));
    if (
      locale !== draft.source_locale &&
      sourceShape !== null &&
      JSON.stringify(skeletonOf(translation.blocks)) !== sourceShape
    ) {
      lessonField("blocks_mismatch");
    }
  }
  return problems;
}

/** Les seuls champs que le serveur accepte : il refuse tout champ inconnu. */
export function customLessonPayload(draft: CustomLessonDraft): CustomLessonDraft {
  return {
    module_id: draft.module_id,
    source_locale: draft.source_locale,
    estimated_minutes: draft.estimated_minutes,
    difficulty: draft.difficulty,
    translations: draft.translations,
  };
}

/**
 * Une leçon livrée se personnalise. Une leçon de l'organisation — copie
 * personnalisée comprise — s'édite directement.
 */
export function canCustomize(lesson: { custom: boolean }): boolean {
  return !lesson.custom;
}

/** La proposition de Nova, lue par l'aperçu comme n'importe quelle leçon. */
export function proposalTranslation(proposal: NovaUpdateProposal): LessonTranslation {
  return { title: proposal.title, description: proposal.description, blocks: proposal.blocks };
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * Une échéance saisie comme un jour vaut la fin de ce jour, à l'heure locale
 * de l'administrateur : « pour le 30 juin » inclut le 30 juin.
 */
export function inputToDueDate(value: string): number | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const [year, month, day] = match.slice(1).map(Number);
  const date = new Date(year, month - 1, day, 23, 59, 0, 0);
  if (date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return Math.floor(date.getTime() / 1000);
}

export function dueDateToInput(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  const date = new Date(seconds * 1000);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Les leçons sous leur module, dans l'ordre du catalogue ; celles de
 *  l'organisation après les leçons livrées, comme les membres les voient. */
export function lessonsByModule<L extends { id: string; module_id: string; custom: boolean }>(view: {
  modules: LearningModuleView[];
  lessons: L[];
}): { module: LearningModuleView; lessons: L[] }[] {
  return view.modules.map((module) => {
    const inModule = view.lessons.filter((lesson) => lesson.module_id === module.id);
    return {
      module,
      lessons: [
        ...inModule.filter((lesson) => !lesson.custom),
        ...inModule.filter((lesson) => lesson.custom),
      ],
    };
  });
}
