/**
 * Limites de l'organisation.
 *
 * La durée maximale d'une dictée, et le plafond de mises au propre par membre
 * et par jour (n° 21 ter). Ils vivent dans la même policy que les
 * interrupteurs de Configuration, avec la même révision : deux écrans ouverts
 * côte à côte ne s'écrasent pas, le serveur refuse la seconde écriture.
 */

import { useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import {
  DEFAULT_DICTATION_MINUTES,
  MAX_DICTATION_MINUTES,
  MAX_REWRITE_CAP,
  MIN_DICTATION_MINUTES,
  capProblem,
  dictationLimitMinutes,
  limitProblem,
  rewriteCap,
  withDictationLimit,
  withRewriteCap,
  type LimitProblem,
  type PolicySettings,
} from "../lib/limits";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Facts, Notice, Panel } from "../components/primitives";

interface LimitPanelProps {
  title: string;
  behaviour: string;
  facts: [string, ReactNode][];
  field: string;
  hint: string;
  min: number;
  max: number;
  current: number;
  problemOf: (input: string) => LimitProblem | null;
  problems: Record<LimitProblem, string>;
  /** Tout le document, avec la nouvelle valeur : le PUT remplace la policy. */
  next: (value: number) => PolicySettings;
  extra?: ReactNode;
  revision: number;
  mayManage: boolean;
  onReload: () => void;
}

/** Une limite : sa valeur, sa saisie et son enregistrement. */
function LimitPanel(props: LimitPanelProps) {
  const { control } = useSession();
  // La saisie en cours, séparée de ce que le serveur a renvoyé.
  const [draft, setDraft] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [saved, setSaved] = useState(false);

  const input = draft ?? String(props.current);
  const problem = props.problemOf(input);
  const dirty =
    draft !== null && (problem !== null || Number(input) !== props.current);

  function reload() {
    setDraft(null);
    setFailure(null);
    setConflict(false);
    setSaved(false);
    props.onReload();
  }

  async function save() {
    if (problem !== null) return;
    setBusy(true);
    setFailure(null);
    setConflict(false);
    try {
      // Sur la révision affichée : le serveur refuse si elle a bougé.
      await control!.updatePolicy(props.revision, props.next(Number(input)));
      setDraft(null);
      setSaved(true);
      props.onReload();
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
    <Panel title={props.title}>
      <p style={{ color: "var(--text-muted)" }}>{props.behaviour}</p>
      <Facts entries={props.facts} />
      <div className="policy-setting">
        <div>
          <strong>{props.field}</strong>
          <p className="policy-hint" style={{ margin: 0 }}>
            {props.hint}
          </p>
        </div>
        <input
          type="number"
          min={props.min}
          max={props.max}
          step={1}
          style={{ width: "10rem" }}
          aria-label={props.field}
          value={input}
          disabled={!props.mayManage || busy}
          onChange={(event) => {
            setSaved(false);
            setDraft(event.target.value);
          }}
        />
      </div>
      {draft !== null && problem !== null && (
        <Notice tone="danger">{props.problems[problem]}</Notice>
      )}
      {props.extra}

      {!props.mayManage && <p className="policy-hint">{t.limits.readOnly}</p>}
      {conflict && (
        <Notice tone="danger">
          {t.policies.conflict}{" "}
          <button onClick={reload}>{t.policies.reload}</button>
        </Notice>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}
      {saved && !dirty && <Notice>{t.policies.saved}</Notice>}

      {props.mayManage && dirty && (
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
}

export function Limits() {
  const { control, session } = useSession();
  const [state, retry] = useResource(control ? () => control.policy() : null, [
    control,
  ]);
  const mayManage = can(session, CAPABILITY.policyManage);

  return (
    <>
      <div className="page-head">
        <h1>{t.limits.title}</h1>
        <p>{t.limits.lead}</p>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => {
          const minutes = dictationLimitMinutes(view.settings);
          const cap = rewriteCap(view.settings);
          return (
            <>
              <LimitPanel
                title={t.limits.dictationTitle}
                behaviour={t.limits.behaviour}
                facts={[
                  [t.limits.current, t.limits.minutes(minutes)],
                  [
                    t.limits.defaultValue,
                    t.limits.minutes(DEFAULT_DICTATION_MINUTES),
                  ],
                ]}
                field={t.limits.fieldTitle}
                hint={t.limits.range(
                  MIN_DICTATION_MINUTES,
                  MAX_DICTATION_MINUTES,
                )}
                min={MIN_DICTATION_MINUTES}
                max={MAX_DICTATION_MINUTES}
                current={minutes}
                problemOf={limitProblem}
                problems={t.limits.problems}
                next={(value) => withDictationLimit(view.settings, value)}
                extra={<Notice>{t.limits.groups}</Notice>}
                revision={view.revision}
                mayManage={mayManage}
                onReload={retry}
              />
              <LimitPanel
                title={t.limits.capTitle}
                behaviour={t.limits.capBehaviour}
                facts={[
                  [t.limits.current, t.limits.capValue(cap)],
                  [t.limits.defaultValue, t.limits.capValue(0)],
                ]}
                field={t.limits.capField}
                hint={t.limits.capRange(MAX_REWRITE_CAP)}
                min={0}
                max={MAX_REWRITE_CAP}
                current={cap}
                problemOf={capProblem}
                problems={t.limits.capProblems}
                next={(value) => withRewriteCap(view.settings, value)}
                revision={view.revision}
                mayManage={mayManage}
                onReload={retry}
              />
            </>
          );
        }}
      </AsyncSection>
    </>
  );
}
