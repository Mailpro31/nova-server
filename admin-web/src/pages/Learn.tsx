/**
 * Apprendre : ce que l'organisation fait du catalogue, et les leçons qu'elle
 * écrit elle-même.
 *
 * ## Une leçon créée se gère comme une leçon livrée
 *
 * Archiver, rendre obligatoire, fixer une échéance, viser des groupes : les
 * mêmes contrôles pour toutes les leçons, parce que le serveur les porte dans
 * la même table. Seules les leçons de l'organisation s'éditent et se
 * suppriment ; une leçon livrée s'archive.
 *
 * ## Pas d'éditeur JSON
 *
 * Chaque bloc a ses champs, et l'aperçu montre la leçon comme un membre la
 * lira. La console ne propose que ce que le serveur accepte, et dit ce qui
 * manque avant d'enregistrer — voir `lib/learning.ts`.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import {
  BLOCK_TEXT_FIELDS,
  DIFFICULTIES,
  EDITABLE_BLOCK_TYPES,
  MAX_BLOCKS,
  MAX_DESCRIPTION,
  MAX_MINUTES,
  MAX_TITLE,
  MIN_MINUTES,
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
  type CustomLessonDraft,
  type CustomLessonView,
  type LearningAdminView,
  type LearningLessonView,
  type LessonBlock,
  type LessonProblem,
  type LessonTranslation,
} from "../lib/learning";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, ConfirmDialog, Notice, Panel, Tag } from "../components/primitives";

interface Option {
  id: string;
  label: string;
}

function failureText(raised: unknown): string {
  return raised instanceof ApiError
    ? errorMessage(raised.kind, raised.code)
    : errorMessage("server");
}

function isConflict(raised: unknown): boolean {
  return raised instanceof ApiError && raised.kind === "conflict";
}

function languageName(code: string): string {
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(code);
    return name && name !== code ? `${name} (${code})` : code;
  } catch {
    return code;
  }
}

function formatDay(seconds: number): string {
  return new Date(seconds * 1000).toLocaleDateString();
}

function textOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optionsOf(block: LessonBlock): Option[] {
  return Array.isArray(block.content.options) ? (block.content.options as Option[]) : [];
}

function isQuiz(type: string): boolean {
  return type === "multiple_choice" || type === "question";
}

function nextOptionId(options: Option[]): string {
  const used = new Set(options.map((option) => option.id));
  for (const letter of "abcdefghijklmnopqrstuvwxyz") {
    if (!used.has(letter)) return letter;
  }
  return `option-${options.length + 1}`;
}

/* ── Page ───────────────────────────────────────────────────────────── */

export function Learn() {
  const { control, session } = useSession();
  const [state, retry] = useResource(control ? () => control.learning() : null, [control]);
  const mayManage = can(session, CAPABILITY.contentManage);
  // `undefined` : la liste. `null` : une nouvelle leçon. Sinon, la leçon ouverte.
  const [editing, setEditing] = useState<string | null | undefined>(undefined);

  return (
    <>
      <div className="page-head">
        <h1>{t.learn.title}</h1>
        <p>{t.learn.lead}</p>
      </div>
      {!mayManage && <p className="policy-hint">{t.learn.readOnly}</p>}
      <AsyncSection state={state} onRetry={retry}>
        {(view) =>
          editing === undefined ? (
            <LessonList
              view={view}
              mayManage={mayManage}
              onChanged={retry}
              onCreate={() => setEditing(null)}
              onEdit={(lessonId) => setEditing(lessonId)}
            />
          ) : (
            <LessonEditor
              key={editing ?? "new"}
              view={view}
              lessonId={editing}
              mayManage={mayManage}
              onClose={(changed) => {
                setEditing(undefined);
                if (changed) retry();
              }}
            />
          )
        }
      </AsyncSection>
    </>
  );
}

/* ── Liste et réglages ──────────────────────────────────────────────── */

function LessonList({
  view,
  mayManage,
  onChanged,
  onCreate,
  onEdit,
}: {
  view: LearningAdminView;
  mayManage: boolean;
  onChanged: () => void;
  onCreate: () => void;
  onEdit: (lessonId: string) => void;
}) {
  return (
    <>
      {mayManage && (
        <div className="row">
          <button data-variant="primary" onClick={onCreate} disabled={view.modules.length === 0}>
            {t.learn.newLesson}
          </button>
        </div>
      )}
      {lessonsByModule(view).map(({ module, lessons }) => (
        <Panel key={module.id} title={`${module.path_title} · ${module.title}`}>
          {lessons.length === 0 ? (
            <p className="policy-hint">{t.learn.emptyModule}</p>
          ) : (
            lessons.map((lesson) => (
              <LessonRow
                key={lesson.id}
                lesson={lesson}
                groups={view.groups}
                mayManage={mayManage}
                onChanged={onChanged}
                onEdit={onEdit}
              />
            ))
          )}
        </Panel>
      ))}
    </>
  );
}

function LessonRow({
  lesson,
  groups,
  mayManage,
  onChanged,
  onEdit,
}: {
  lesson: LearningLessonView;
  groups: { id: string; label: string }[];
  mayManage: boolean;
  onChanged: () => void;
  onEdit: (lessonId: string) => void;
}) {
  const { control } = useSession();
  const [open, setOpen] = useState(false);
  const [required, setRequired] = useState(lesson.required);
  const [due, setDue] = useState(dueDateToInput(lesson.due_at));
  const [groupIds, setGroupIds] = useState<string[]>(lesson.group_ids);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const archived = lesson.status === "archived";

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    setConflict(false);
    try {
      await action();
      // La liste est relue : l'écran montre ce que le serveur porte.
      onChanged();
    } catch (raised) {
      if (isConflict(raised)) setConflict(true);
      else setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  if (confirmArchive) {
    return (
      <ConfirmDialog
        title={t.learn.archiveTitle}
        body={t.learn.archiveBody}
        confirmLabel={t.learn.archive}
        busy={busy}
        error={conflict ? t.learn.conflict : failure}
        onConfirm={() => void run(() => control!.archiveLearningLesson(lesson.id, lesson.revision))}
        onCancel={() => setConfirmArchive(false)}
      />
    );
  }

  return (
    <div className="lesson-row" data-archived={archived || undefined}>
      <div className="row spread">
        <div className="stack lesson-summary">
          <strong>{lesson.title}</strong>
          <div className="row">
            {lesson.custom && <Tag>{t.learn.custom}</Tag>}
            {archived && <Tag tone="warn">{t.learn.archived}</Tag>}
            {lesson.required && <Tag tone="ok">{t.learn.required}</Tag>}
            {lesson.due_at !== null && <Tag>{t.learn.dueOn(formatDay(lesson.due_at))}</Tag>}
            <Tag>
              {lesson.group_ids.length === 0
                ? t.learn.groupsAll
                : t.learn.groupsSome(lesson.group_ids.length)}
            </Tag>
          </div>
        </div>
        {mayManage && (
          <div className="row">
            {lesson.custom && (
              <button disabled={busy} onClick={() => onEdit(lesson.id)}>
                {t.learn.edit}
              </button>
            )}
            <button disabled={busy} aria-expanded={open} onClick={() => setOpen(!open)}>
              {t.learn.settings}
            </button>
            {archived ? (
              <button
                disabled={busy}
                onClick={() =>
                  void run(() => control!.restoreLearningLesson(lesson.id, lesson.revision))
                }
              >
                {t.learn.restore}
              </button>
            ) : (
              <button disabled={busy} onClick={() => setConfirmArchive(true)}>
                {t.learn.archive}
              </button>
            )}
          </div>
        )}
      </div>

      {open && mayManage && (
        <div className="lesson-settings">
          <label className="row">
            <input
              type="checkbox"
              checked={required}
              disabled={busy}
              onChange={(event) => setRequired(event.target.checked)}
            />
            <span>{t.learn.requiredLabel}</span>
          </label>
          <label className="field">
            <span>{t.learn.dueDate}</span>
            <input
              type="date"
              value={due}
              disabled={busy}
              onChange={(event) => setDue(event.target.value)}
            />
          </label>
          {groups.length > 0 && (
            <fieldset className="stack group-picker">
              <legend>{t.learn.groups}</legend>
              {groups.map((group) => (
                <label key={group.id} className="row">
                  <input
                    type="checkbox"
                    checked={groupIds.includes(group.id)}
                    disabled={busy}
                    onChange={(event) =>
                      setGroupIds(
                        event.target.checked
                          ? [...groupIds, group.id]
                          : groupIds.filter((id) => id !== group.id),
                      )
                    }
                  />
                  <span>{group.label}</span>
                </label>
              ))}
              <p className="policy-hint">{t.learn.groupsHint}</p>
            </fieldset>
          )}
          <div className="row">
            <button
              data-variant="primary"
              disabled={busy}
              onClick={() =>
                void run(() =>
                  control!.updateLearningLesson(lesson.id, lesson.revision, {
                    required,
                    due_at: inputToDueDate(due),
                    group_ids: groupIds,
                  }),
                )
              }
            >
              {busy ? t.learn.saving : t.learn.saveSettings}
            </button>
            <button data-variant="quiet" disabled={busy} onClick={() => setOpen(false)}>
              {t.learn.cancel}
            </button>
          </div>
        </div>
      )}

      {conflict && (
        <Notice tone="danger">
          {t.learn.conflict} <button onClick={onChanged}>{t.learn.reload}</button>
        </Notice>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}
    </div>
  );
}

/* ── Éditeur ────────────────────────────────────────────────────────── */

function LessonEditor({
  view,
  lessonId,
  mayManage,
  onClose,
}: {
  view: LearningAdminView;
  lessonId: string | null;
  mayManage: boolean;
  onClose: (changed: boolean) => void;
}) {
  if (lessonId === null) {
    return <EditorForm view={view} initial={null} mayManage={mayManage} onClose={onClose} />;
  }
  return (
    <ExistingLessonEditor view={view} lessonId={lessonId} mayManage={mayManage} onClose={onClose} />
  );
}

function ExistingLessonEditor({
  view,
  lessonId,
  mayManage,
  onClose,
}: {
  view: LearningAdminView;
  lessonId: string;
  mayManage: boolean;
  onClose: (changed: boolean) => void;
}) {
  const { control } = useSession();
  const [state, retry] = useResource(
    control ? () => control.customLesson(lessonId) : null,
    [control, lessonId],
  );
  return (
    <div className="stack">
      <div>
        <button data-variant="quiet" onClick={() => onClose(false)}>
          {t.learn.close}
        </button>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(lesson) => (
          <EditorForm
            view={view}
            initial={lesson}
            mayManage={mayManage}
            onClose={onClose}
            onReload={retry}
          />
        )}
      </AsyncSection>
    </div>
  );
}

function EditorForm({
  view,
  initial,
  mayManage,
  onClose,
  onReload,
}: {
  view: LearningAdminView;
  initial: CustomLessonView | null;
  mayManage: boolean;
  onClose: (changed: boolean) => void;
  onReload?: () => void;
}) {
  const { control } = useSession();
  const [draft, setDraft] = useState<CustomLessonDraft>(() =>
    initial
      ? customLessonPayload(initial)
      : emptyCustomLesson(
          view.modules[0]?.id ?? "",
          view.locales.includes("en") ? "en" : (view.locales[0] ?? "en"),
        ),
  );
  const [locale, setLocale] = useState(draft.source_locale);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [newBlockType, setNewBlockType] = useState<string>(EDITABLE_BLOCK_TYPES[0]);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const problems = lessonProblems(draft);
  const editable = mayManage && !busy;
  const current = draft.translations[locale] ? locale : draft.source_locale;
  const isSource = current === draft.source_locale;
  const translation: LessonTranslation = draft.translations[current];
  const sourceBlocks = draft.translations[draft.source_locale]?.blocks ?? [];
  const languages = Object.keys(draft.translations).sort();
  const missingLanguages = view.locales.filter((code) => !(code in draft.translations));

  function patch(next: Partial<CustomLessonDraft>) {
    setDraft((previous) => ({ ...previous, ...next }));
  }

  function setTranslation(code: string, next: Partial<LessonTranslation>) {
    setDraft((previous) => ({
      ...previous,
      translations: {
        ...previous.translations,
        [code]: { ...previous.translations[code], ...next },
      },
    }));
  }

  /** Les blocs se décident dans la langue source ; chaque traduction suit. */
  function setSourceBlocks(blocks: LessonBlock[]) {
    setDraft((previous) => {
      const translations: Record<string, LessonTranslation> = {};
      for (const [code, entry] of Object.entries(previous.translations)) {
        translations[code] =
          code === previous.source_locale
            ? { ...entry, blocks }
            : { ...entry, blocks: alignTranslation(blocks, entry.blocks) };
      }
      return { ...previous, translations };
    });
  }

  function setBlockContent(index: number, content: Record<string, unknown>) {
    if (isSource) {
      setSourceBlocks(
        sourceBlocks.map((block, position) => (position === index ? { ...block, content } : block)),
      );
      return;
    }
    setTranslation(current, {
      blocks: translation.blocks.map((block, position) =>
        position === index ? { ...block, content } : block,
      ),
    });
  }

  function setSourceLocale(code: string) {
    setDraft((previous) => {
      const blocks = previous.translations[code].blocks;
      const translations: Record<string, LessonTranslation> = {};
      for (const [key, entry] of Object.entries(previous.translations)) {
        translations[key] =
          key === code ? entry : { ...entry, blocks: alignTranslation(blocks, entry.blocks) };
      }
      return { ...previous, source_locale: code, translations };
    });
    setLocale(code);
  }

  /** Une langue ajoutée part du texte source : il reste visible ce qui est à traduire. */
  function addLanguage(code: string) {
    if (!code) return;
    const source = draft.translations[draft.source_locale];
    setTranslation(code, {
      title: source.title,
      description: source.description,
      blocks: alignTranslation(source.blocks, []),
    });
    setLocale(code);
  }

  function removeLanguage(code: string) {
    if (code === draft.source_locale) return;
    setDraft((previous) => {
      const translations = { ...previous.translations };
      delete translations[code];
      return { ...previous, translations };
    });
    setLocale(draft.source_locale);
  }

  function problemText(problem: LessonProblem): string {
    const where = [problem.locale ? languageName(problem.locale) : t.learn.lessonWide];
    if (problem.blockId && problem.locale) {
      const blocks = draft.translations[problem.locale]?.blocks ?? [];
      const index = blocks.findIndex((block) => block.id === problem.blockId);
      if (index >= 0) where.push(`${index + 1}. ${t.learn.blockLabel(blocks[index].type)}`);
    }
    return `${where.join(" · ")}: ${t.learn.problemLabel(problem.field)}`;
  }

  async function save() {
    if (problems.length > 0) return;
    setBusy(true);
    setFailure(null);
    setConflict(false);
    try {
      const payload = customLessonPayload(draft);
      if (initial) await control!.updateCustomLesson(initial.id, initial.revision, payload);
      else await control!.createCustomLesson(payload);
      onClose(true);
    } catch (raised) {
      if (raised instanceof ApiError && raised.kind === "conflict" && initial) setConflict(true);
      else setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!initial) return;
    setBusy(true);
    setFailure(null);
    try {
      await control!.deleteCustomLesson(initial.id, initial.revision);
      onClose(true);
    } catch (raised) {
      setFailure(isConflict(raised) ? t.learn.conflict : failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  if (confirmDelete && initial) {
    return (
      <ConfirmDialog
        title={t.learn.deleteTitle}
        body={t.learn.deleteBody}
        confirmLabel={t.learn.delete}
        tone="danger"
        busy={busy}
        error={failure}
        onConfirm={() => void remove()}
        onCancel={() => {
          setConfirmDelete(false);
          setFailure(null);
        }}
      />
    );
  }

  return (
    <Panel
      title={initial ? t.learn.editTitle : t.learn.newTitle}
      action={
        initial ? undefined : (
          <button data-variant="quiet" disabled={busy} onClick={() => onClose(false)}>
            {t.learn.close}
          </button>
        )
      }
    >
      <div className="row lesson-meta">
        <label className="field">
          <span>{t.learn.module}</span>
          <select
            value={draft.module_id}
            disabled={!editable}
            onChange={(event) => patch({ module_id: event.target.value })}
          >
            {view.modules.map((module) => (
              <option key={module.id} value={module.id}>
                {`${module.path_title} · ${module.title}`}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t.learn.sourceLanguage}</span>
          <select
            value={draft.source_locale}
            disabled={!editable}
            onChange={(event) => setSourceLocale(event.target.value)}
          >
            {languages.map((code) => (
              <option key={code} value={code}>
                {languageName(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>{t.learn.minutes}</span>
          <input
            type="number"
            min={MIN_MINUTES}
            max={MAX_MINUTES}
            value={draft.estimated_minutes}
            disabled={!editable}
            onChange={(event) => patch({ estimated_minutes: Number(event.target.value) })}
          />
        </label>
        <label className="field">
          <span>{t.learn.difficulty}</span>
          <select
            value={draft.difficulty}
            disabled={!editable}
            onChange={(event) => patch({ difficulty: event.target.value })}
          >
            {DIFFICULTIES.map((difficulty) => (
              <option key={difficulty} value={difficulty}>
                {t.learn.difficultyLabel(difficulty)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="stack">
        <h3 className="policy-group">{t.learn.languages}</h3>
        <div className="row" role="tablist" aria-label={t.learn.languages}>
          {languages.map((code) => (
            <button
              key={code}
              role="tab"
              aria-selected={code === current}
              data-variant={code === current ? "primary" : undefined}
              onClick={() => setLocale(code)}
            >
              {languageName(code)}
              {code === draft.source_locale ? ` · ${t.learn.sourceBadge}` : ""}
            </button>
          ))}
          {editable && missingLanguages.length > 0 && (
            <select
              className="language-add"
              aria-label={t.learn.addLanguage}
              value=""
              onChange={(event) => addLanguage(event.target.value)}
            >
              <option value="">{t.learn.addLanguage}</option>
              {missingLanguages.map((code) => (
                <option key={code} value={code}>
                  {languageName(code)}
                </option>
              ))}
            </select>
          )}
        </div>
        {!isSource && <Notice>{t.learn.translationNote}</Notice>}
        {!isSource && editable && (
          <div>
            <button data-variant="quiet" onClick={() => removeLanguage(current)}>
              {t.learn.removeLanguage}
            </button>
          </div>
        )}
      </div>

      <div className="lesson-editor">
        <div className="stack editor-column">
          <label className="field">
            <span>{t.learn.lessonTitle}</span>
            <input
              value={translation.title}
              maxLength={MAX_TITLE}
              disabled={!editable}
              onChange={(event) => setTranslation(current, { title: event.target.value })}
            />
          </label>
          <label className="field">
            <span>{t.learn.lessonDescription}</span>
            <textarea
              rows={2}
              value={translation.description}
              maxLength={MAX_DESCRIPTION}
              disabled={!editable}
              onChange={(event) => setTranslation(current, { description: event.target.value })}
            />
          </label>

          <h3 className="policy-group">{t.learn.blocks}</h3>
          {isSource && editable && <p className="policy-hint">{t.learn.dragHint}</p>}
          {translation.blocks.map((block, index) => (
            <div
              key={block.id}
              className="block-card"
              draggable={isSource && editable}
              data-dragging={dragIndex === index || undefined}
              onDragStart={() => setDragIndex(index)}
              onDragEnd={() => setDragIndex(null)}
              onDragOver={(event) => {
                if (dragIndex !== null) event.preventDefault();
              }}
              onDrop={(event) => {
                event.preventDefault();
                if (dragIndex !== null) setSourceBlocks(moveBlock(sourceBlocks, dragIndex, index));
                setDragIndex(null);
              }}
            >
              <div className="row spread">
                <strong>{`${index + 1}. ${t.learn.blockLabel(block.type)}`}</strong>
                {isSource && editable && (
                  <div className="row">
                    <button
                      data-variant="quiet"
                      aria-label={t.learn.moveUp}
                      title={t.learn.moveUp}
                      disabled={index === 0}
                      onClick={() => setSourceBlocks(moveBlock(sourceBlocks, index, index - 1))}
                    >
                      ↑
                    </button>
                    <button
                      data-variant="quiet"
                      aria-label={t.learn.moveDown}
                      title={t.learn.moveDown}
                      disabled={index === translation.blocks.length - 1}
                      onClick={() => setSourceBlocks(moveBlock(sourceBlocks, index, index + 1))}
                    >
                      ↓
                    </button>
                    <button
                      data-variant="quiet"
                      onClick={() => setSourceBlocks(removeBlock(sourceBlocks, index))}
                    >
                      {t.learn.removeBlock}
                    </button>
                  </div>
                )}
              </div>
              <BlockFields
                block={block}
                structural={isSource}
                disabled={!editable}
                onChange={(content) => setBlockContent(index, content)}
              />
            </div>
          ))}
          {isSource && editable && (
            <div className="row">
              <select
                className="block-type"
                aria-label={t.learn.blockType}
                value={newBlockType}
                onChange={(event) => setNewBlockType(event.target.value)}
              >
                {EDITABLE_BLOCK_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {t.learn.blockLabel(type)}
                  </option>
                ))}
              </select>
              <button
                disabled={sourceBlocks.length >= MAX_BLOCKS}
                onClick={() => setSourceBlocks([...sourceBlocks, emptyBlock(newBlockType, sourceBlocks)])}
              >
                {t.learn.addBlock}
              </button>
            </div>
          )}
        </div>

        <aside className="stack preview-column" aria-label={t.learn.preview}>
          <h3 className="policy-group">{t.learn.preview}</h3>
          <p className="policy-hint">{t.learn.previewNote}</p>
          <LessonPreview translation={translation} minutes={draft.estimated_minutes} />
        </aside>
      </div>

      {problems.length > 0 && (
        <div className="notice">
          <strong>{t.learn.problemsTitle}</strong>
          <ul className="problem-list">
            {problems.slice(0, 12).map((problem, index) => (
              <li key={index}>{problemText(problem)}</li>
            ))}
          </ul>
        </div>
      )}
      {conflict && (
        <Notice tone="danger">
          {t.learn.conflict}{" "}
          <button onClick={() => (onReload ? onReload() : onClose(true))}>{t.learn.reload}</button>
        </Notice>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}

      {mayManage && (
        <div className="row spread">
          <div className="row">
            <button
              data-variant="primary"
              disabled={busy || problems.length > 0}
              onClick={() => void save()}
            >
              {busy ? t.learn.saving : t.learn.save}
            </button>
            <button data-variant="quiet" disabled={busy} onClick={() => onClose(false)}>
              {t.learn.cancel}
            </button>
          </div>
          {initial && (
            <button data-variant="danger" disabled={busy} onClick={() => setConfirmDelete(true)}>
              {t.learn.delete}
            </button>
          )}
        </div>
      )}
    </Panel>
  );
}

function BlockFields({
  block,
  structural,
  disabled,
  onChange,
}: {
  block: LessonBlock;
  /** Langue source : les réponses s'ajoutent, se retirent et se choisissent ici. */
  structural: boolean;
  disabled: boolean;
  onChange: (content: Record<string, unknown>) => void;
}) {
  const set = (field: string, value: unknown) => onChange({ ...block.content, [field]: value });
  const quiz = isQuiz(block.type);
  const options = optionsOf(block);
  const fields = (BLOCK_TEXT_FIELDS[block.type] ?? []).filter(
    (field) => !(quiz && field === "explanation"),
  );

  return (
    <div className="stack">
      {fields.map((field) => (
        <label key={field} className="field">
          <span>{t.learn.fieldLabel(field)}</span>
          <textarea
            rows={field === "body" || field === "sample_answer" || field === "starting_text" ? 3 : 1}
            value={textOf(block.content[field])}
            disabled={disabled}
            onChange={(event) => set(field, event.target.value)}
          />
        </label>
      ))}
      {quiz && (
        <fieldset className="stack option-list">
          <legend>{t.learn.options}</legend>
          {options.map((option, index) => (
            <div key={option.id} className="row option-row">
              <input
                type="radio"
                name={`correct-${block.id}`}
                aria-label={`${t.learn.correct} ${index + 1}`}
                checked={block.content.correct_option_id === option.id}
                disabled={disabled || !structural}
                onChange={() => set("correct_option_id", option.id)}
              />
              <input
                aria-label={`${t.learn.options} ${index + 1}`}
                value={option.label}
                disabled={disabled}
                onChange={(event) =>
                  set(
                    "options",
                    options.map((candidate) =>
                      candidate.id === option.id
                        ? { ...candidate, label: event.target.value }
                        : candidate,
                    ),
                  )
                }
              />
              {structural && options.length > 2 && (
                <button
                  data-variant="quiet"
                  disabled={disabled}
                  onClick={() =>
                    onChange({
                      ...block.content,
                      options: options.filter((candidate) => candidate.id !== option.id),
                      correct_option_id:
                        block.content.correct_option_id === option.id
                          ? ""
                          : block.content.correct_option_id,
                    })
                  }
                >
                  {t.learn.removeOption}
                </button>
              )}
            </div>
          ))}
          {structural && (
            <div>
              <button
                data-variant="quiet"
                disabled={disabled || options.length >= 6}
                onClick={() => set("options", [...options, { id: nextOptionId(options), label: "" }])}
              >
                {t.learn.addOption}
              </button>
            </div>
          )}
          <p className="policy-hint">{t.learn.correctHint}</p>
        </fieldset>
      )}
      {quiz && (
        <label className="field">
          <span>{t.learn.fieldLabel("explanation")}</span>
          <textarea
            rows={2}
            value={textOf(block.content.explanation)}
            disabled={disabled}
            onChange={(event) => set("explanation", event.target.value)}
          />
        </label>
      )}
    </div>
  );
}

/* ── Aperçu ─────────────────────────────────────────────────────────── */

function LessonPreview({
  translation,
  minutes,
}: {
  translation: LessonTranslation;
  minutes: number;
}) {
  return (
    <article className="lesson-preview">
      <h2>{translation.title || t.learn.untitled}</h2>
      {translation.description && <p className="preview-muted">{translation.description}</p>}
      <p className="policy-hint">{t.learn.minutesShort(minutes)}</p>
      {[...translation.blocks]
        .sort((left, right) => left.order - right.order)
        .map((block) => (
          <PreviewBlock key={block.id} block={block} />
        ))}
    </article>
  );
}

function PreviewBlock({ block }: { block: LessonBlock }) {
  const content = block.content;
  if (block.type === "comparison") {
    return (
      <div className="preview-comparison">
        <div>
          <small>{textOf(content.worse_label) || t.learn.worseDefault}</small>
          <p>{textOf(content.worse)}</p>
        </div>
        <div>
          <small>{textOf(content.better_label) || t.learn.betterDefault}</small>
          <p>{textOf(content.better)}</p>
        </div>
        {textOf(content.note) && <p className="preview-muted">{textOf(content.note)}</p>}
      </div>
    );
  }
  if (isQuiz(block.type)) {
    return (
      <div className="preview-callout">
        <strong>{textOf(content.question)}</strong>
        <ul>
          {optionsOf(block).map((option) => (
            <li key={option.id} data-correct={option.id === content.correct_option_id || undefined}>
              {option.label}
            </li>
          ))}
        </ul>
        {textOf(content.explanation) && (
          <p className="preview-muted">{textOf(content.explanation)}</p>
        )}
      </div>
    );
  }
  if (block.type === "reflection") {
    return (
      <div className="preview-callout">
        <strong>{textOf(content.prompt)}</strong>
      </div>
    );
  }
  if (block.type === "prompt_exercise") {
    return (
      <div className="preview-callout">
        <strong>{textOf(content.instruction)}</strong>
        {textOf(content.starting_text) && <p className="mono">{textOf(content.starting_text)}</p>}
        {textOf(content.hint) && <p className="preview-muted">{textOf(content.hint)}</p>}
      </div>
    );
  }
  return (
    <div className="preview-block" data-type={block.type}>
      {textOf(content.heading) && <strong>{textOf(content.heading)}</strong>}
      <p>{textOf(content.body)}</p>
    </div>
  );
}
