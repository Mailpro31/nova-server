/**
 * Deployment → AI Runtime.
 *
 * ## Deux blocs, jamais fondus
 *
 * `desired` est ce qu'un administrateur a demandé ; `reported` est ce que le
 * runtime confirme. Les afficher ensemble ferait croire qu'enregistrer, c'est
 * appliquer — et sur un déploiement où le plan de contrôle ne peut pas encore
 * joindre le serveur privé, ce serait faux. L'écran le dit lui-même plutôt que
 * de le laisser à un document que personne n'ouvrira.
 *
 * ## Ce que cette page n'est pas
 *
 * Pas une console vLLM. Un profil décrit un comportement voulu ; quel modèle le
 * sert appartient à la configuration du runtime. Aucun nom commercial de modèle
 * n'est écrit ici — ce qui s'affiche vient de ce que le runtime rapporte.
 *
 * ## Ce qui n'y entre jamais
 *
 * Aucune donnée utilisateur. Ni requête, ni transcription, ni sortie de modèle.
 * C'est précisément la page où l'on serait tenté d'en ajouter « pour aider au
 * diagnostic ».
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { AIRuntimeEngineView } from "../lib/controlPlane";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Facts, Notice, Panel, Tag } from "../components/primitives";

const RUNTIME_TONE: Record<string, "ok" | "warn" | "danger" | undefined> = {
  healthy: "ok",
  degraded: "warn",
  starting: "warn",
  unavailable: "danger",
};

const ENGINE_TONE: Record<string, "ok" | "warn" | "danger" | undefined> = {
  ready: "ok",
  loading: "warn",
  unavailable: "danger",
};

function runtimeLabel(status: string): string {
  if (status === "healthy") return t.aiRuntime.statusHealthy;
  if (status === "degraded") return t.aiRuntime.statusDegraded;
  if (status === "starting") return t.aiRuntime.statusStarting;
  return t.aiRuntime.statusUnavailable;
}

function engineLabel(status: string): string {
  if (status === "ready") return t.aiRuntime.engineReady;
  if (status === "loading") return t.aiRuntime.engineLoading;
  return t.aiRuntime.engineUnavailable;
}

/**
 * Les profils que la console sait nommer.
 *
 * Lesquels sont *proposes* ne se decide pas ici : le serveur envoie
 * `available_profiles`, et l'ecran n'affiche que ceux-la. `custom` en
 * particulier n'existe que si le deploiement a declare ses parametres —
 * l'offrir partout en ferait une option qui ne regle rien.
 */
const PROFILE_LABELS: Record<string, string> = {
  efficient: t.aiRuntime.profileEfficient,
  standard: t.aiRuntime.profileStandard,
  advanced: t.aiRuntime.profileAdvanced,
  custom: t.aiRuntime.profileCustom,
};

/** Les valeurs du mode automatique, identiques à celles que le serveur écrit. */
const AUTO_CONCURRENCY = { text: 2, speech: 1, queue: 4 };

/**
 * Détail d'un moteur, sans rien inventer.
 *
 * Un champ que le runtime ne rapporte pas s'affiche « pas encore configuré »
 * plutôt que rempli d'une valeur plausible : ici, une valeur fausse enverrait
 * chercher une panne au mauvais endroit.
 */
function engineFacts(engine: AIRuntimeEngineView): [string, React.ReactNode][] {
  return [
    [t.aiRuntime.adapter, <span className="mono">{engine.adapter}</span>],
    [
      t.aiRuntime.model,
      engine.model ? <span className="mono">{engine.model}</span> : t.state.notConfigured,
    ],
    [
      t.aiRuntime.runtimeVersion,
      engine.runtime_version ? (
        <span className="mono">{engine.runtime_version}</span>
      ) : (
        t.state.notConfigured
      ),
    ],
    [t.aiRuntime.latency, engine.latency_ms === null ? "—" : `${engine.latency_ms} ms`],
    [
      t.aiRuntime.lastError,
      engine.last_error_code ? <span className="mono">{engine.last_error_code}</span> : "—",
    ],
  ];
}

export function AIRuntime() {
  const { control, session } = useSession();
  const [state, retry] = useResource(control ? () => control.aiRuntime() : null, [control]);
  const mayManage = can(session, CAPABILITY.deploymentManage);

  // Les modifications en cours, séparées de ce que le serveur a renvoyé : sans
  // cette séparation, on ne saurait pas dire ce qui est enregistré.
  const [draft, setDraft] = useState<{
    profile: string;
    reasoning_allowed: boolean;
    concurrency_mode: "auto" | "manual";
    max_concurrent_text: number;
    max_concurrent_speech: number;
    max_queue: number;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  function reload() {
    setDraft(null);
    setFailure(null);
    setSaved(false);
    retry();
  }

  return (
    <AsyncSection state={state} onRetry={retry}>
      {(view) => {
        const current = draft ?? {
          profile: view.desired.profile,
          reasoning_allowed: view.desired.reasoning_allowed,
          concurrency_mode:
            view.desired.max_concurrent_text === AUTO_CONCURRENCY.text &&
            view.desired.max_concurrent_speech === AUTO_CONCURRENCY.speech &&
            view.desired.max_queue === AUTO_CONCURRENCY.queue
              ? ("auto" as const)
              : ("manual" as const),
          max_concurrent_text: view.desired.max_concurrent_text,
          max_concurrent_speech: view.desired.max_concurrent_speech,
          max_queue: view.desired.max_queue,
        };
        const dirty = draft !== null;
        // Le serveur dit où part le texte (n° 21 bis) : la page ne l'affirme plus.
        const external = view.processing_location === "external_ai_provider";
        const applied =
          view.desired.revision > 0 &&
          view.reported.applied_revision === view.desired.revision;

        function set(patch: Partial<typeof current>) {
          setSaved(false);
          setDraft({ ...current, ...patch });
        }

        async function save() {
          setBusy(true);
          setFailure(null);
          try {
            // La révision sur laquelle cet écran a été construit. Le serveur
            // refuse si elle a bougé — il n'écrase jamais en silence.
            await control!.updateAIRuntime(view.desired.revision, current);
            setDraft(null);
            setSaved(true);
            // Re-lecture autoritative : l'écran montre ce que le runtime
            // rapporte, jamais l'écho de ce qui vient d'être envoyé.
            retry();
          } catch (raised) {
            setFailure(
              raised instanceof ApiError
                ? errorMessage(raised.kind, raised.code)
                : errorMessage("server"),
            );
          } finally {
            setBusy(false);
          }
        }

        return (
          <>
            <Panel title={t.aiRuntime.title}>
              <p style={{ color: "var(--text-muted)" }}>{t.aiRuntime.lead}</p>
              {/* La frontière, dite à l'écran. C'est la question que pose toute
                  DSI devant cette page, et la réponse ne doit pas dépendre d'un
                  document qu'elle n'a pas ouvert. */}
              <Notice>
                {external ? t.aiRuntime.privacyExternal : t.aiRuntime.privacy}
              </Notice>
              <Facts
                entries={[
                  [
                    t.aiRuntime.processing,
                    external
                      ? t.aiRuntime.externalProvider
                      : t.aiRuntime.onPremises,
                  ],
                  [
                    t.deployment.mode,
                    <span className="mono">{view.deployment_mode}</span>,
                  ],
                  [
                    t.aiRuntime.cloudFallback,
                    <Tag tone="ok">{t.aiRuntime.cloudFallbackDisabled}</Tag>,
                  ],
                ]}
              />
              <p className="policy-hint">
                {external
                  ? t.aiRuntime.cloudFallbackExternalBody
                  : t.aiRuntime.cloudFallbackBody}
              </p>
            </Panel>

            <Panel title={t.aiRuntime.statusTitle}>
              <Facts
                entries={[
                  [
                    t.aiRuntime.status,
                    <Tag tone={RUNTIME_TONE[view.reported.runtime_status]}>
                      {runtimeLabel(view.reported.runtime_status)}
                    </Tag>,
                  ],
                  [
                    t.aiRuntime.textEngine,
                    <Tag tone={ENGINE_TONE[view.reported.text_engine.status]}>
                      {engineLabel(view.reported.text_engine.status)}
                    </Tag>,
                  ],
                  [
                    t.aiRuntime.speechEngine,
                    <Tag tone={ENGINE_TONE[view.reported.speech_engine.status]}>
                      {engineLabel(view.reported.speech_engine.status)}
                    </Tag>,
                  ],
                  [
                    t.aiRuntime.capacity,
                    <span className="mono">
                      {view.reported.capacity.text.active}/
                      {view.reported.capacity.text.max_concurrent}
                      {" · "}
                      {view.reported.capacity.text.queue_length}
                    </span>,
                  ],
                  [
                    t.aiRuntime.lastCheck,
                    view.reported.last_health_check
                      ? new Date(view.reported.last_health_check * 1000).toLocaleString()
                      : t.aiRuntime.never,
                  ],
                ]}
              />
              <details>
                <summary>{t.aiRuntime.advanced}</summary>
                <h3 className="policy-group">{t.aiRuntime.textEngine}</h3>
                <Facts entries={engineFacts(view.reported.text_engine)} />
                <h3 className="policy-group">{t.aiRuntime.speechEngine}</h3>
                <Facts entries={engineFacts(view.reported.speech_engine)} />
              </details>
            </Panel>

            <Panel title={t.aiRuntime.hardwareTitle}>
              <Facts
                entries={[
                  [
                    t.aiRuntime.cpu,
                    <span className="mono">{view.hardware.cpu || "—"}</span>,
                  ],
                  [
                    t.aiRuntime.architecture,
                    <span className="mono">{view.hardware.architecture}</span>,
                  ],
                  [t.aiRuntime.ram, view.hardware.ram_gb ? `${view.hardware.ram_gb} GB` : "—"],
                  [
                    t.aiRuntime.gpu,
                    view.hardware.gpu_model ? (
                      <span className="mono">{view.hardware.gpu_model}</span>
                    ) : (
                      t.aiRuntime.noGpu
                    ),
                  ],
                  [
                    t.aiRuntime.vram,
                    view.hardware.vram_gb ? `${view.hardware.vram_gb} GB` : "—",
                  ],
                  [
                    t.aiRuntime.driver,
                    view.hardware.driver_version ? (
                      <span className="mono">{view.hardware.driver_version}</span>
                    ) : (
                      "—"
                    ),
                  ],
                  [t.aiRuntime.recommended, <Tag>{view.recommended_profile}</Tag>],
                ]}
              />
              {/* Recommandé, jamais appliqué tout seul : le profil engage la
                  latence et la charge de tout le parc. C'est une décision. */}
              <p className="policy-hint">{t.aiRuntime.recommendedBody}</p>
            </Panel>

            <Panel title={t.aiRuntime.configTitle}>
              <div className="policy-setting">
                <div>
                  <strong>{t.aiRuntime.profile}</strong>
                  <p style={{ color: "var(--text-muted)", margin: 0 }}>
                    {t.aiRuntime.profileBody}
                  </p>
                </div>
                <select
                  aria-label={t.aiRuntime.profile}
                  value={current.profile}
                  disabled={!mayManage || busy}
                  onChange={(event) => set({ profile: event.target.value })}
                >
                  {view.available_profiles.map((profile) => (
                    <option key={profile} value={profile}>
                      {PROFILE_LABELS[profile] ?? profile}
                    </option>
                  ))}
                </select>
              </div>
              {/* Dire pourquoi une option manque vaut mieux que la laisser
                  chercher. Sans cette phrase, « Custom » ressemble a une
                  fonction retiree plutot qu'a une configuration a declarer. */}
              {!view.available_profiles.includes("custom") && (
                <p className="policy-hint">{t.aiRuntime.customUnavailable}</p>
              )}

              <div className="policy-setting">
                <div>
                  <strong>{t.aiRuntime.reasoning}</strong>
                  <p style={{ color: "var(--text-muted)", margin: 0 }}>
                    {t.aiRuntime.reasoningBody}
                  </p>
                </div>
                <label className="row">
                  <input
                    type="checkbox"
                    aria-label={t.aiRuntime.reasoning}
                    checked={current.reasoning_allowed}
                    disabled={!mayManage || busy}
                    onChange={(event) => set({ reasoning_allowed: event.target.checked })}
                  />
                  <span>
                    {current.reasoning_allowed ? t.policies.enabled : t.policies.disabled}
                  </span>
                </label>
              </div>

              <div className="policy-setting">
                <div>
                  <strong>{t.aiRuntime.concurrency}</strong>
                  <p style={{ color: "var(--text-muted)", margin: 0 }}>
                    {t.aiRuntime.concurrencyBody}
                  </p>
                </div>
                <select
                  aria-label={t.aiRuntime.concurrency}
                  value={current.concurrency_mode}
                  disabled={!mayManage || busy}
                  onChange={(event) =>
                    set({ concurrency_mode: event.target.value as "auto" | "manual" })
                  }
                >
                  <option value="auto">{t.aiRuntime.concurrencyAuto}</option>
                  <option value="manual">{t.aiRuntime.concurrencyManual}</option>
                </select>
              </div>

              {current.concurrency_mode === "manual" && (
                <Facts
                  entries={[
                    [
                      t.aiRuntime.concurrencyText,
                      <input
                        type="number"
                        min={1}
                        max={64}
                        aria-label={t.aiRuntime.concurrencyText}
                        value={current.max_concurrent_text}
                        disabled={!mayManage || busy}
                        onChange={(event) =>
                          set({ max_concurrent_text: Number(event.target.value) })
                        }
                      />,
                    ],
                    [
                      t.aiRuntime.concurrencySpeech,
                      <input
                        type="number"
                        min={1}
                        max={32}
                        aria-label={t.aiRuntime.concurrencySpeech}
                        value={current.max_concurrent_speech}
                        disabled={!mayManage || busy}
                        onChange={(event) =>
                          set({ max_concurrent_speech: Number(event.target.value) })
                        }
                      />,
                    ],
                    [
                      t.aiRuntime.concurrencyQueue,
                      <input
                        type="number"
                        min={0}
                        max={256}
                        aria-label={t.aiRuntime.concurrencyQueue}
                        value={current.max_queue}
                        disabled={!mayManage || busy}
                        onChange={(event) => set({ max_queue: Number(event.target.value) })}
                      />,
                    ],
                  ]}
                />
              )}

              {!mayManage && <p className="policy-hint">{t.aiRuntime.readOnly}</p>}
              {failure && <Notice tone="danger">{failure}</Notice>}
              {saved && !dirty && <Notice>{t.aiRuntime.saved}</Notice>}
              {mayManage && dirty && (
                <>
                  <Notice>{t.aiRuntime.unsaved}</Notice>
                  <div className="row">
                    <button disabled={busy} onClick={() => void save()}>
                      {busy ? t.aiRuntime.saving : t.aiRuntime.save}
                    </button>
                    <button disabled={busy} onClick={reload}>
                      {t.aiRuntime.discard}
                    </button>
                  </div>
                </>
              )}
            </Panel>

            {view.custom_profile.available && (
              <Panel title={t.aiRuntime.customTitle}>
                {/* Lecture seule, et le dire. Ces valeurs viennent de la
                    configuration du serveur ; les rendre modifiables ici
                    ouvrirait depuis un navigateur la surface que la Phase 32
                    garde cote exploitation. */}
                <p style={{ color: "var(--text-muted)" }}>{t.aiRuntime.customBody}</p>
                <Facts
                  entries={[
                    [
                      t.aiRuntime.customTokens,
                      <span className="mono">
                        {view.custom_profile.max_output_tokens}
                      </span>,
                    ],
                    [
                      t.aiRuntime.customTemperature,
                      <span className="mono">{view.custom_profile.temperature}</span>,
                    ],
                    [
                      t.aiRuntime.customReasoning,
                      <span className="mono">{view.custom_profile.reasoning}</span>,
                    ],
                  ]}
                />
              </Panel>
            )}

            <Panel title={t.aiRuntime.stateTitle}>
              {/* « Enregistré » et « appliqué » sont deux faits différents. Les
                  confondre ferait chercher une panne dans le modèle alors que
                  la configuration n'a jamais atteint le runtime. */}
              <Facts
                entries={[
                  [
                    t.aiRuntime.desiredRevision,
                    <span className="mono">{view.desired.revision}</span>,
                  ],
                  [
                    t.aiRuntime.appliedRevision,
                    <span className="mono">{view.reported.applied_revision}</span>,
                  ],
                  [
                    t.aiRuntime.applyStatus,
                    applied ? (
                      <Tag tone="ok">{t.aiRuntime.appliedLocal}</Tag>
                    ) : view.desired.revision === 0 ? (
                      <Tag>{t.aiRuntime.localDefault}</Tag>
                    ) : (
                      <Tag tone="warn">{t.aiRuntime.pending}</Tag>
                    ),
                  ],
                ]}
              />
              {/* La frontière de la Phase 33, dite ici plutôt que découverte
                  plus tard : ce plan de contrôle ne reconfigure pas encore un
                  serveur privé distant, et prétendre le contraire produirait un
                  « Applied » que rien ne confirme. */}
              {!view.remote_apply.supported && (
                <Notice
                  tone={view.remote_apply.requires_private_connector ? "danger" : undefined}
                >
                  {view.remote_apply.requires_private_connector
                    ? t.aiRuntime.awaitingConnector
                    : t.aiRuntime.remoteUnsupported}
                </Notice>
              )}
            </Panel>
          </>
        );
      }}
    </AsyncSection>
  );
}
