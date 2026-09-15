/**
 * Réglages par groupe.
 *
 * Sous les réglages de l'organisation, et construits sur la **même table** :
 * un réglage ajouté à l'un apparaît dans l'autre. Chaque réglage offre trois
 * choix — suivre l'organisation, activé pour ce groupe, désactivé pour ce
 * groupe — parce que « activé » et « suit l'organisation, qui l'active » ne
 * disent pas la même chose le jour où l'organisation change d'avis.
 *
 * La règle entre deux groupes d'un même membre est dite à l'écran : c'est la
 * première question qu'on se pose, et la réponse ne se devine pas.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { GroupPolicyView } from "../lib/controlPlane";
import {
  applyChoice,
  choiceOf,
  countOverrides,
  dictationMinutes,
  sameOverrides,
  withDictationMinutes,
  type GroupOverrides,
  type OverrideChoice,
} from "../lib/groupPolicies";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Notice, Panel } from "../components/primitives";

export interface PolicyCatalogSection {
  title: string;
  items: { key: string; title: string; body: string }[];
}

export function GroupPolicies({ catalog }: { catalog: PolicyCatalogSection[] }) {
  const { control, session } = useSession();
  const [state, retry] = useResource(
    control ? () => control.groupPolicies() : null,
    [control],
  );
  const mayManage = can(session, CAPABILITY.policyManage);
  const [open, setOpen] = useState<string | null>(null);

  return (
    <Panel title={t.groupPolicies.title}>
      <p style={{ color: "var(--text-muted)" }}>{t.groupPolicies.lead}</p>
      <Notice>{t.groupPolicies.restrictive}</Notice>
      <AsyncSection state={state} onRetry={retry}>
        {(view) =>
          view.groups.length === 0 ? (
            <p className="policy-hint">{t.groupPolicies.empty}</p>
          ) : (
            <div className="stack">
              {view.groups.map((group) =>
                open === group.id ? (
                  <GroupEditor
                    key={group.id}
                    group={group}
                    catalog={catalog}
                    mayManage={mayManage}
                    onClose={(changed) => {
                      setOpen(null);
                      if (changed) retry();
                    }}
                  />
                ) : (
                  <div className="policy-setting" key={group.id}>
                    <div>
                      <strong>{group.label}</strong>
                      <p className="policy-hint" style={{ margin: 0 }}>
                        {countOverrides(group.overrides) === 0
                          ? t.groupPolicies.followsOrganization
                          : t.groupPolicies.overrideCount(countOverrides(group.overrides))}
                      </p>
                    </div>
                    <button onClick={() => setOpen(group.id)}>
                      {mayManage ? t.groupPolicies.edit : t.groupPolicies.view}
                    </button>
                  </div>
                ),
              )}
            </div>
          )
        }
      </AsyncSection>
    </Panel>
  );
}

function GroupEditor({
  group,
  catalog,
  mayManage,
  onClose,
}: {
  group: GroupPolicyView;
  catalog: PolicyCatalogSection[];
  mayManage: boolean;
  onClose: (changed: boolean) => void;
}) {
  const { control } = useSession();
  // Les choix en cours, séparés de ce que le serveur a renvoyé.
  const [draft, setDraft] = useState<GroupOverrides>(group.overrides);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const dirty = !sameOverrides(draft, group.overrides);
  const editable = mayManage && !busy;

  async function save() {
    setBusy(true);
    setFailure(null);
    setConflict(false);
    try {
      // La révision sur laquelle l'écran a été construit : le serveur refuse
      // si elle a bougé, il n'écrase jamais en silence.
      await control!.updateGroupPolicy(group.id, group.revision, draft);
      onClose(true);
    } catch (raised) {
      if (raised instanceof ApiError && raised.kind === "conflict") {
        setConflict(true);
      } else {
        setFailure(
          raised instanceof ApiError
            ? errorMessage(raised.kind, raised.code)
            : errorMessage("server"),
        );
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="lesson-settings">
      <div className="row spread">
        <strong>{group.label}</strong>
        <button data-variant="quiet" disabled={busy} onClick={() => onClose(false)}>
          {t.groupPolicies.close}
        </button>
      </div>

      {catalog.map((section) => (
        <div key={section.title}>
          <h3 className="policy-group">{section.title}</h3>
          {section.items.map((item) => (
            <div className="policy-setting" key={item.key}>
              <div>
                <strong>{item.title}</strong>
              </div>
              <select
                className="block-type"
                aria-label={item.title}
                value={choiceOf(draft, item.key)}
                disabled={!editable}
                onChange={(event) =>
                  setDraft(applyChoice(draft, item.key, event.target.value as OverrideChoice))
                }
              >
                <option value="inherit">{t.groupPolicies.inherit}</option>
                <option value="on">{t.groupPolicies.on}</option>
                <option value="off">{t.groupPolicies.off}</option>
              </select>
            </div>
          ))}
        </div>
      ))}

      <h3 className="policy-group">{t.groupPolicies.limits}</h3>
      <div className="policy-setting">
        <div>
          <strong>{t.groupPolicies.dictationTitle}</strong>
          <p className="policy-hint" style={{ margin: 0 }}>
            {t.groupPolicies.dictationBody}
          </p>
        </div>
        <input
          type="number"
          min={1}
          max={120}
          style={{ width: "10rem" }}
          aria-label={t.groupPolicies.dictationTitle}
          placeholder={t.groupPolicies.inherit}
          value={dictationMinutes(draft) ?? ""}
          disabled={!editable}
          onChange={(event) =>
            setDraft(
              withDictationMinutes(
                draft,
                event.target.value === "" ? null : Number(event.target.value),
              ),
            )
          }
        />
      </div>

      {!mayManage && <p className="policy-hint">{t.groupPolicies.readOnly}</p>}
      {conflict && (
        <Notice tone="danger">
          {t.groupPolicies.conflict}{" "}
          <button onClick={() => onClose(true)}>{t.groupPolicies.reload}</button>
        </Notice>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}
      {mayManage && dirty && (
        <>
          <Notice>{t.groupPolicies.unsaved}</Notice>
          <div className="row">
            <button data-variant="primary" disabled={busy} onClick={() => void save()}>
              {busy ? t.groupPolicies.saving : t.groupPolicies.save}
            </button>
            <button data-variant="quiet" disabled={busy} onClick={() => setDraft(group.overrides)}>
              {t.groupPolicies.cancel}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
