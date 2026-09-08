/**
 * Contenu distribué par l'organisation : Styles, AI Skills, vocabulaire.
 *
 * ## Package n'est pas Policy
 *
 * Une policy dit **si** une capacité est autorisée ; un package dit **quoi**
 * l'organisation distribue. On ne trouvera donc ici aucun interrupteur
 * « activé » : la permission vit dans Configuration → Product policies, et un
 * package publié reste sans effet si la policy correspondante est fermée. La
 * page le dit à l'écran, faute de quoi on chercherait longtemps pourquoi un
 * contenu publié n'arrive pas.
 *
 * ## Publier n'écrase jamais
 *
 * Une version publiée est immuable. Modifier demande un brouillon ; revenir en
 * arrière ne réécrit rien, cela redésigne une version restée intacte. C'est ce
 * qui rend le retour arrière sûr : on republie ce qui avait été publié, pas une
 * reconstruction de mémoire.
 *
 * Pas d'éditeur JSON : le serveur possède le schéma, et chaque type a le
 * contrôle qui lui convient — un formulaire pour un Style, un tableau pour le
 * vocabulaire.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { PackageView, PackageVersionDetail } from "../lib/controlPlane";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import {
  AsyncSection,
  ConfirmDialog,
  Facts,
  Notice,
  Panel,
  Tag,
} from "../components/primitives";

type Draft = Record<string, unknown>;

/** Un document neuf, par type. Le serveur refusera tout ce qui n'est pas ça. */
function emptyContent(packageType: string): Draft {
  if (packageType === "style") return { name: "", instruction: "" };
  if (packageType === "ai_skill")
    return {
      title: "",
      summary: "",
      practice: "",
      duration_minutes: 5,
      steps: [],
    };
  return { entries: [{ term: "", replacement: "" }] };
}

function statusTone(status: string): "ok" | "warn" | undefined {
  if (status === "published") return "ok";
  if (status === "draft") return "warn";
  return undefined;
}

/* ── Éditeurs par type ──────────────────────────────────────────────── */

function StyleEditor({
  content,
  onChange,
  disabled,
}: {
  content: Draft;
  onChange: (next: Draft) => void;
  disabled: boolean;
}) {
  return (
    <>
      <label className="field">
        <span>{t.packages.styleName}</span>
        <input
          value={String(content.name ?? "")}
          disabled={disabled}
          onChange={(event) => onChange({ ...content, name: event.target.value })}
        />
      </label>
      <label className="field">
        <span>{t.packages.styleInstruction}</span>
        <textarea
          rows={6}
          value={String(content.instruction ?? "")}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...content, instruction: event.target.value })
          }
        />
      </label>
    </>
  );
}

function AiSkillEditor({
  content,
  onChange,
  disabled,
}: {
  content: Draft;
  onChange: (next: Draft) => void;
  disabled: boolean;
}) {
  const steps = Array.isArray(content.steps) ? (content.steps as string[]) : [];
  return (
    <>
      <label className="field">
        <span>{t.packages.skillTitle}</span>
        <input
          value={String(content.title ?? "")}
          disabled={disabled}
          onChange={(event) => onChange({ ...content, title: event.target.value })}
        />
      </label>
      <label className="field">
        <span>{t.packages.skillSummary}</span>
        <textarea
          rows={3}
          value={String(content.summary ?? "")}
          disabled={disabled}
          onChange={(event) => onChange({ ...content, summary: event.target.value })}
        />
      </label>
      {/* Le champ qui fait d'une fiche une action. Placé avant `practice`
          parce qu'il est le seul obligatoire : l'ordre du formulaire doit
          suivre ce qui est exigé, sinon on remplit le mauvais champ. */}
      <label className="field">
        <span>{t.packages.skillInstruction}</span>
        <textarea
          rows={5}
          value={String(content.instruction ?? "")}
          placeholder={t.packages.skillInstructionHint}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...content, instruction: event.target.value })
          }
        />
      </label>
      <label className="field">
        <span>{t.packages.skillPractice}</span>
        <textarea
          rows={3}
          value={String(content.practice ?? "")}
          placeholder={t.packages.skillPracticeHint}
          disabled={disabled}
          onChange={(event) => onChange({ ...content, practice: event.target.value })}
        />
      </label>
      <label className="field">
        <span>{t.packages.skillDuration}</span>
        <input
          type="number"
          min={1}
          max={240}
          value={Number(content.duration_minutes ?? 5)}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              ...content,
              duration_minutes: Number(event.target.value) || 1,
            })
          }
        />
      </label>
      <div className="field">
        <span>{t.packages.skillSteps}</span>
        {steps.map((step, index) => (
          <div className="row" key={index}>
            <input
              value={step}
              disabled={disabled}
              onChange={(event) => {
                const next = [...steps];
                next[index] = event.target.value;
                onChange({ ...content, steps: next });
              }}
            />
            <button
              data-variant="quiet"
              disabled={disabled}
              onClick={() =>
                onChange({
                  ...content,
                  steps: steps.filter((_, position) => position !== index),
                })
              }
            >
              {t.packages.removeRow}
            </button>
          </div>
        ))}
        <div className="row">
          <button
            disabled={disabled}
            onClick={() => onChange({ ...content, steps: [...steps, ""] })}
          >
            {t.packages.addStep}
          </button>
        </div>
      </div>
    </>
  );
}

function VocabularyEditor({
  content,
  onChange,
  disabled,
}: {
  content: Draft;
  onChange: (next: Draft) => void;
  disabled: boolean;
}) {
  const entries = Array.isArray(content.entries)
    ? (content.entries as { term?: string; replacement?: string }[])
    : [];
  function update(index: number, patch: Record<string, string>) {
    const next = entries.map((entry, position) =>
      position === index ? { ...entry, ...patch } : entry,
    );
    onChange({ ...content, entries: next });
  }
  return (
    <div className="field">
      <span>{t.packages.vocabularyEntries}</span>
      {entries.map((entry, index) => (
        <div className="row" key={index}>
          <input
            aria-label={t.packages.vocabularyTerm}
            placeholder={t.packages.vocabularyTerm}
            value={entry.term ?? ""}
            disabled={disabled}
            onChange={(event) => update(index, { term: event.target.value })}
          />
          <input
            aria-label={t.packages.vocabularyReplacement}
            placeholder={t.packages.vocabularyReplacement}
            value={entry.replacement ?? ""}
            disabled={disabled}
            onChange={(event) => update(index, { replacement: event.target.value })}
          />
          <button
            data-variant="quiet"
            disabled={disabled}
            onClick={() =>
              onChange({
                ...content,
                entries: entries.filter((_, position) => position !== index),
              })
            }
          >
            {t.packages.removeRow}
          </button>
        </div>
      ))}
      <div className="row">
        <button
          disabled={disabled}
          onClick={() =>
            onChange({
              ...content,
              entries: [...entries, { term: "", replacement: "" }],
            })
          }
        >
          {t.packages.addEntry}
        </button>
      </div>
    </div>
  );
}

function ContentEditor(props: {
  packageType: string;
  content: Draft;
  onChange: (next: Draft) => void;
  disabled: boolean;
}) {
  if (props.packageType === "style") return <StyleEditor {...props} />;
  if (props.packageType === "ai_skill") return <AiSkillEditor {...props} />;
  return <VocabularyEditor {...props} />;
}

/* ── Page ───────────────────────────────────────────────────────────── */

export function Packages() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const [state, retry] = useResource(
    control && organizationId ? () => control.packages(organizationId) : null,
    [control, organizationId],
  );
  const mayManage = can(session, CAPABILITY.contentManage);
  const [open, setOpen] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  function report(raised: unknown) {
    if (raised instanceof ApiError) {
      // Le serveur nomme le champ fautif — la console le montre plutôt que de
      // dire « erreur », qui n'aide personne à corriger.
      const detail = raised.detail as { message?: string; field?: string } | null;
      setFailure(
        detail?.message
          ? `${detail.message}${detail.field ? ` (${detail.field})` : ""}`
          : errorMessage(raised.kind, raised.code),
      );
    } else {
      setFailure(errorMessage("server"));
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>{t.packages.title}</h1>
        <p>{t.packages.lead}</p>
      </div>
      {/* La confusion à éviter en premier : publier n'autorise pas. */}
      <Notice>{t.packages.policyBoundary}</Notice>
      {failure && <Notice tone="danger">{failure}</Notice>}

      {mayManage && (
        <CreatePackage
          organizationId={organizationId}
          onCreated={() => {
            setFailure(null);
            retry();
          }}
          onError={report}
        />
      )}

      <AsyncSection state={state} onRetry={retry}>
        {(view) =>
          view.packages.length === 0 ? (
            <Notice>{t.packages.empty}</Notice>
          ) : (
            <>
              {view.packages.map((entry) => (
                <PackageCard
                  key={entry.package_id}
                  organizationId={organizationId}
                  entry={entry}
                  mayManage={mayManage}
                  expanded={open === entry.package_id}
                  onToggle={() =>
                    setOpen(open === entry.package_id ? null : entry.package_id)
                  }
                  onChanged={() => {
                    setFailure(null);
                    retry();
                  }}
                  onError={report}
                />
              ))}
            </>
          )
        }
      </AsyncSection>
      {!mayManage && <p className="policy-hint">{t.packages.readOnly}</p>}
    </>
  );
}

function CreatePackage({
  organizationId,
  onCreated,
  onError,
}: {
  organizationId: string;
  onCreated: () => void;
  onError: (raised: unknown) => void;
}) {
  const { control } = useSession();
  const [packageType, setPackageType] = useState("vocabulary");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      await control!.createPackage(organizationId, {
        package_type: packageType,
        name,
        description,
      });
      setName("");
      setDescription("");
      onCreated();
    } catch (raised) {
      onError(raised);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title={t.packages.createTitle}>
      {/* Trois champs, pas un assistant en huit étapes. */}
      <div className="row">
        <label className="field">
          <span>{t.packages.type}</span>
          <select
            value={packageType}
            disabled={busy}
            onChange={(event) => setPackageType(event.target.value)}
          >
            <option value="vocabulary">{t.packages.typeVocabulary}</option>
            <option value="style">{t.packages.typeStyle}</option>
            <option value="ai_skill">{t.packages.typeAiSkill}</option>
          </select>
        </label>
        <label className="field">
          <span>{t.packages.name}</span>
          <input
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className="field">
          <span>{t.packages.description}</span>
          <input
            value={description}
            disabled={busy}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
      </div>
      <div className="row">
        <button disabled={busy || !name.trim()} onClick={() => void create()}>
          {t.packages.create}
        </button>
      </div>
    </Panel>
  );
}

function PackageCard({
  organizationId,
  entry,
  mayManage,
  expanded,
  onToggle,
  onChanged,
  onError,
}: {
  organizationId: string;
  entry: PackageView;
  mayManage: boolean;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => void;
  onError: (raised: unknown) => void;
}) {
  const { control } = useSession();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [detail, setDetail] = useState<PackageVersionDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmPublish, setConfirmPublish] = useState<string | null>(null);
  const [confirmActivate, setConfirmActivate] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  const draftVersion = entry.versions.find((v) => v.status === "draft") ?? null;
  const dirty = draft !== null && detail !== null;

  async function guard(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      setDraft(null);
      setDetail(null);
      onChanged();
    } catch (raised) {
      onError(raised);
    } finally {
      setBusy(false);
    }
  }

  async function openDraft() {
    if (!draftVersion) {
      await guard(() =>
        control!.createPackageDraft(organizationId, entry.package_id, {
          content: emptyContent(entry.package_type),
        }),
      );
      return;
    }
    setBusy(true);
    try {
      const loaded = await control!.packageVersion(
        organizationId,
        entry.package_id,
        draftVersion.version_id,
      );
      setDetail(loaded);
      setDraft(loaded.content as Draft);
    } catch (raised) {
      onError(raised);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title={entry.name}
      action={
        <button data-variant="quiet" onClick={onToggle}>
          {expanded ? t.packages.close : t.packages.open}
        </button>
      }
    >
      <Facts
        entries={[
          [t.packages.type, t.packages.typeLabel(entry.package_type)],
          [
            t.packages.status,
            // Le libellé, pas seulement une couleur : un état ne doit pas se
            // deviner à la teinte.
            <Tag tone={statusTone(entry.status)}>
              {t.packages.statusLabel(entry.status)}
            </Tag>,
          ],
          [
            t.packages.activeVersion,
            entry.active_version_number
              ? `v${entry.active_version_number}`
              : t.state.notConfigured,
          ],
          [t.packages.description, entry.description || t.state.notConfigured],
        ]}
      />

      {expanded && (
        <>
          <h3 className="policy-group">{t.packages.versions}</h3>
          {entry.versions.length === 0 && (
            <p className="policy-hint">{t.packages.noVersion}</p>
          )}
          {entry.versions.map((version) => (
            <div className="policy-setting" key={version.version_id}>
              <div>
                <strong>v{version.version_number}</strong>{" "}
                <Tag tone={statusTone(version.status)}>
                  {t.packages.statusLabel(version.status)}
                </Tag>{" "}
                {version.active && <Tag tone="ok">{t.packages.active}</Tag>}
                {/* Un brouillon incomplet est créable — mais on doit le savoir
                    avant de cliquer sur Publier, pas après le refus. */}
                {version.validation_status === "invalid" && (
                  <Tag tone="warn">{t.packages.incomplete}</Tag>
                )}
              </div>
              {mayManage && (
                <div className="row">
                  {version.status === "draft" && (
                    <>
                      {/* Dire pourquoi le bouton est éteint. Un bouton
                          désactivé sans explication se lit comme une panne —
                          et c'est ainsi qu'il a été lu en recette. */}
                      {version.validation_status === "invalid" && (
                        <span className="policy-hint">
                          {t.packages.completeFirst}
                        </span>
                      )}
                      <button
                        disabled={busy || version.validation_status === "invalid"}
                        onClick={() => setConfirmPublish(version.version_id)}
                      >
                        {t.packages.publish}
                      </button>
                      {/* Les actions du brouillon vivent sur sa ligne, et non
                          seulement dans l'éditeur : on ne devrait pas avoir à
                          ouvrir un brouillon pour l'abandonner. */}
                      <button
                        data-variant="quiet"
                        disabled={busy}
                        onClick={() =>
                          void guard(() =>
                            control!.discardPackageDraft(
                              organizationId,
                              entry.package_id,
                              version.version_id,
                            ),
                          )
                        }
                      >
                        {t.packages.deleteDraft}
                      </button>
                    </>
                  )}
                  {version.status === "published" && !version.active && (
                    <button
                      disabled={busy}
                      onClick={() => setConfirmActivate(version.version_id)}
                    >
                      {t.packages.activate}
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}

          {entry.status === "archived" && (
            <Notice>{t.packages.archivedNote}</Notice>
          )}

          {mayManage && (
            <div className="row">
              <button disabled={busy} onClick={() => void openDraft()}>
                {draftVersion ? t.packages.editDraft : t.packages.newDraft}
              </button>
              {entry.status === "archived" ? (
                <button
                  disabled={busy}
                  onClick={() =>
                    void guard(() =>
                      control!.restorePackage(organizationId, entry.package_id),
                    )
                  }
                >
                  {t.packages.restore}
                </button>
              ) : (
                // Confirmée : elle retire le contenu à tous les membres, et se
                // trouve juste à côté des actions d'édition.
                <button
                  data-variant="quiet"
                  disabled={busy}
                  onClick={() => setConfirmArchive(true)}
                >
                  {t.packages.archive}
                </button>
              )}
            </div>
          )}

          {dirty && draft && detail && (
            <>
              <h3 className="policy-group">
                {t.packages.editing} v{detail.version_number}
              </h3>
              <div className="version-editor">
                <ContentEditor
                  packageType={entry.package_type}
                  content={draft}
                  onChange={setDraft}
                  disabled={busy}
                />
              </div>
              <Notice>{t.packages.unsaved}</Notice>
              <div className="row">
                <button
                  disabled={busy}
                  onClick={() =>
                    void guard(() =>
                      control!.updatePackageDraft(
                        organizationId,
                        entry.package_id,
                        detail.version_id,
                        { expected_revision: detail.revision, content: draft },
                      ),
                    )
                  }
                >
                  {t.packages.save}
                </button>
                <button
                  data-variant="quiet"
                  disabled={busy}
                  onClick={() => {
                    setDraft(null);
                    setDetail(null);
                  }}
                >
                  {t.packages.discard}
                </button>
              </div>
            </>
          )}
        </>
      )}

      {confirmPublish && (
        <ConfirmDialog
          title={t.packages.publishTitle}
          body={t.packages.publishBody(
            entry.versions.find((v) => v.version_id === confirmPublish)
              ?.version_number ?? 0,
          )}
          confirmLabel={t.packages.publish}
          onCancel={() => setConfirmPublish(null)}
          onConfirm={() => {
            const version = confirmPublish;
            setConfirmPublish(null);
            void guard(() =>
              control!.publishPackageVersion(
                organizationId,
                entry.package_id,
                version,
              ),
            );
          }}
        />
      )}
      {confirmArchive && (
        <ConfirmDialog
          title={t.packages.archiveTitle}
          body={t.packages.archiveBody}
          confirmLabel={t.packages.archive}
          onCancel={() => setConfirmArchive(false)}
          onConfirm={() => {
            setConfirmArchive(false);
            void guard(() =>
              control!.archivePackage(organizationId, entry.package_id),
            );
          }}
        />
      )}
      {confirmActivate && (
        <ConfirmDialog
          title={t.packages.activateTitle}
          body={t.packages.activateBody(
            entry.versions.find((v) => v.version_id === confirmActivate)
              ?.version_number ?? 0,
          )}
          confirmLabel={t.packages.activate}
          onCancel={() => setConfirmActivate(null)}
          onConfirm={() => {
            const version = confirmActivate;
            setConfirmActivate(null);
            void guard(() =>
              control!.activatePackageVersion(
                organizationId,
                entry.package_id,
                version,
              ),
            );
          }}
        />
      )}
    </Panel>
  );
}
