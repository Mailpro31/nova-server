/**
 * Limites de l'organisation.
 *
 * Un seul réglage pour l'instant : la durée maximale d'une dictée. Il vit dans
 * la même policy que les interrupteurs de Configuration, avec la même
 * révision : deux écrans ouverts côte à côte ne s'écrasent pas, le serveur
 * refuse la seconde écriture.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import {
  DEFAULT_DICTATION_MINUTES,
  MAX_DICTATION_MINUTES,
  MIN_DICTATION_MINUTES,
  dictationLimitMinutes,
  limitProblem,
  withDictationLimit,
} from "../lib/limits";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Facts, Notice, Panel } from "../components/primitives";

export function Limits() {
  const { control, session } = useSession();
  const [state, retry] = useResource(control ? () => control.policy() : null, [
    control,
  ]);
  const mayManage = can(session, CAPABILITY.policyManage);

  // La saisie en cours, séparée de ce que le serveur a renvoyé.
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [saved, setSaved] = useState(false);

  function reload() {
    setDraft(null);
    setFailure(null);
    setConflict(false);
    setSaved(false);
    retry();
  }

  return (
    <>
      <div className="page-head">
        <h1>{t.limits.title}</h1>
        <p>{t.limits.lead}</p>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => {
          const current = dictationLimitMinutes(view.settings);
          const input = draft ?? String(current);
          const problem = limitProblem(input);
          const dirty =
            draft !== null && (problem !== null || Number(input) !== current);

          async function save() {
            if (problem !== null) return;
            const minutes = Number(input);
            setBusy(true);
            setFailure(null);
            setConflict(false);
            try {
              // Tout le document, sur la révision affichée : le PUT remplace la
              // policy et le serveur refuse si elle a bougé entre-temps.
              const next = withDictationLimit(view.settings, minutes);
              await control!.updatePolicy(view.revision, next);
              setDraft(null);
              setSaved(true);
              retry();
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
            <Panel title={t.limits.dictationTitle}>
              <p style={{ color: "var(--text-muted)" }}>{t.limits.behaviour}</p>
              <Facts
                entries={[
                  [t.limits.current, t.limits.minutes(current)],
                  [
                    t.limits.defaultValue,
                    t.limits.minutes(DEFAULT_DICTATION_MINUTES),
                  ],
                ]}
              />
              <div className="policy-setting">
                <div>
                  <strong>{t.limits.fieldTitle}</strong>
                  <p className="policy-hint" style={{ margin: 0 }}>
                    {t.limits.range(
                      MIN_DICTATION_MINUTES,
                      MAX_DICTATION_MINUTES,
                    )}
                  </p>
                </div>
                <input
                  type="number"
                  min={MIN_DICTATION_MINUTES}
                  max={MAX_DICTATION_MINUTES}
                  step={1}
                  style={{ width: "10rem" }}
                  aria-label={t.limits.fieldTitle}
                  value={input}
                  disabled={!mayManage || busy}
                  onChange={(event) => {
                    setSaved(false);
                    setDraft(event.target.value);
                  }}
                />
              </div>
              {draft !== null && problem !== null && (
                <Notice tone="danger">{t.limits.problems[problem]}</Notice>
              )}
              <Notice>{t.limits.groups}</Notice>

              {!mayManage && <p className="policy-hint">{t.limits.readOnly}</p>}
              {conflict && (
                <Notice tone="danger">
                  {t.policies.conflict}{" "}
                  <button onClick={reload}>{t.policies.reload}</button>
                </Notice>
              )}
              {failure && <Notice tone="danger">{failure}</Notice>}
              {saved && !dirty && <Notice>{t.policies.saved}</Notice>}

              {mayManage && dirty && (
                <div className="row">
                  <button
                    data-variant="primary"
                    disabled={busy || problem !== null}
                    onClick={() => void save()}
                  >
                    {busy ? t.policies.saving : t.policies.save}
                  </button>
                  <button data-variant="quiet" disabled={busy} onClick={reload}>
                    {t.policies.discard}
                  </button>
                </div>
              )}
            </Panel>
          );
        }}
      </AsyncSection>
    </>
  );
}
