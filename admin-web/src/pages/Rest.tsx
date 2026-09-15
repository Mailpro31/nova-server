/**
 * Deployment, Configuration, Security, Diagnostics.
 *
 * Regroupées : elles sont courtes, partagent leurs sources, et les séparer en
 * quatre fichiers de trente lignes n'aiderait personne à s'y retrouver.
 *
 * Aucune ne fabrique de donnée. Quand le serveur ne fournit pas quelque chose,
 * la page le dit — « pas encore configuré » est une information, un faux chiffre
 * n'en est pas une.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import { auditChanges, formatAuditValue } from "../lib/audit";
import type { AuditEntry } from "../lib/controlPlane";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can, secondsRemaining } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Facts, Notice, Panel, Tag } from "../components/primitives";
import { AIRuntime } from "./AIRuntime";
import { PrivateConnector } from "./Connector";
import { DesktopDeployment } from "./DesktopDeployment";

function formatDuration(seconds: number): string {
  if (seconds <= 0) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}

/* ── Deployment ─────────────────────────────────────────────────────── */

export function Deployment() {
  const { control } = useSession();
  const [state, retry] = useResource(
    control ? () => control.organization() : null,
    [control],
  );

  return (
    <>
      <div className="page-head">
        <h1>{t.deployment.title}</h1>
        <p>{t.deployment.lead}</p>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => (
          <>
            <Panel title={t.deployment.title}>
              <Facts
                entries={[
                  [t.deployment.mode, view.deployment.deployment_mode],
                  [t.overview.serverMode, view.deployment.server_mode],
                  [
                    t.deployment.endpoint,
                    view.discovery.service_endpoint ? (
                      <span className="mono">{view.discovery.service_endpoint}</span>
                    ) : (
                      t.state.notConfigured
                    ),
                  ],
                  [
                    t.deployment.slug,
                    view.discovery.slug ? (
                      <span className="mono">{view.discovery.slug}</span>
                    ) : (
                      t.state.notConfigured
                    ),
                  ],
                ]}
              />
            </Panel>
            <Panel title={t.deployment.fleetTitle}>
              {/* Le parc reste hors de portée : aucun poste ne rapporte son
                  état à Nova. Ce qui suit décrit donc le *package* et sa
                  configuration, jamais la santé des machines. */}
              <Notice>{t.deployment.fleetBody}</Notice>
            </Panel>
          </>
        )}
      </AsyncSection>
      {/* Comment une DSI installe le poste. Avant le Connector : on installe
          d'abord, on configure ensuite. */}
      <DesktopDeployment />
      {/* Comment ce deploiement recoit sa configuration, puis ce qu'il en
          fait. Le Connector vient avant le runtime : sans lui, la
          configuration du runtime attend. */}
      <PrivateConnector />
      {/* Ou tourne reellement l'IA de cette organisation. Sa place est ici :
          c'est une propriete du deploiement, pas un reglage produit. */}
      <AIRuntime />
    </>
  );
}

/* ── Configuration ──────────────────────────────────────────────────── */

export function Configuration() {
  const { control, session } = useSession();
  const [state, retry] = useResource(
    control ? () => control.discoveryConfig() : null,
    [control],
  );
  const mayManage = can(session, CAPABILITY.discoveryManage);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function toggleDiscovery(enabled: boolean) {
    setBusy(true);
    setFailure(null);
    try {
      await control!.updateDiscovery({ discovery_enabled: enabled });
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
      <div className="page-head">
        <h1>{t.configuration.title}</h1>
        <p>{t.configuration.lead}</p>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => (
          <Panel title={t.configuration.discoveryTitle}>
            <p style={{ color: "var(--text-muted)" }}>{t.configuration.discoveryBody}</p>
            <Facts
              entries={[
                [
                  t.configuration.discoveryEnabled,
                  view.discovery_enabled ? (
                    <Tag tone="ok">{t.identity.enabled}</Tag>
                  ) : (
                    <Tag>{t.identity.disabled}</Tag>
                  ),
                ],
                [
                  t.deployment.slug,
                  view.discovery_slug ? (
                    <span className="mono">{view.discovery_slug}</span>
                  ) : (
                    t.state.notConfigured
                  ),
                ],
                [
                  t.deployment.endpoint,
                  view.service_endpoint ? (
                    <span className="mono">{view.service_endpoint}</span>
                  ) : (
                    t.state.notConfigured
                  ),
                ],
              ]}
            />
            {view.service_endpoint_error && (
              <Notice tone="danger">
                {t.configuration.endpointInvalid}{" "}
                <span className="mono">{view.service_endpoint_error}</span>
              </Notice>
            )}
            {failure && <Notice tone="danger">{failure}</Notice>}
            {mayManage && (
              <div className="row">
                <button
                  disabled={busy}
                  onClick={() => void toggleDiscovery(!view.discovery_enabled)}
                >
                  {view.discovery_enabled
                    ? t.configuration.disableDiscovery
                    : t.configuration.enableDiscovery}
                </button>
              </div>
            )}
          </Panel>
        )}
      </AsyncSection>
      <Policies />
    </>
  );
}

/* ── Product policies ───────────────────────────────────────────────── */

/**
 * Les policies, groupées par usage.
 *
 * Une table plutôt que du JSX répété : ajouter une policy revient à ajouter
 * une ligne, et l'écran ne peut pas en oublier une au fil des modifications.
 * Le regroupement suit ce qu'un administrateur cherche — « ce qui touche à
 * l'écriture » — pas l'ordre du schéma serveur.
 */
const POLICY_GROUPS: {
  title: string;
  items: { key: string; title: string; body: string }[];
}[] = [
  {
    title: t.policies.groupAi,
    items: [
      {
        key: "ai_skills_enabled",
        title: t.policies.aiSkillsTitle,
        body: t.policies.aiSkillsBody,
      },
    ],
  },
  {
    title: t.policies.groupWriting,
    items: [
      {
        key: "organization_vocabulary_enabled",
        title: t.policies.vocabularyTitle,
        body: t.policies.vocabularyBody,
      },
      {
        key: "engineering_notes_enabled",
        title: t.policies.notesTitle,
        body: t.policies.notesBody,
      },
    ],
  },
  {
    title: t.policies.groupInput,
    items: [
      {
        key: "voice_commands_enabled",
        title: t.policies.commandsTitle,
        body: t.policies.commandsBody,
      },
      {
        key: "file_import_enabled",
        title: t.policies.fileImportTitle,
        // La question qu'on se pose en lisant « import audio » : est-ce que
        // cela coupe la dictée ? Non, et l'écran le dit.
        body: `${t.policies.fileImportBody} ${t.policies.dictationSafe}`,
      },
    ],
  },
  {
    title: t.policies.groupCategories,
    items: [
      {
        key: "styles_enabled",
        title: t.policies.stylesTitle,
        body: t.policies.stylesBody,
      },
      {
        key: "generated_instructions_enabled",
        title: t.policies.generatedInstructionsTitle,
        body: `${t.policies.generatedInstructionsBody} ${t.policies.deviceCategoryNote}`,
      },
      {
        key: "past_dictations_enabled",
        title: t.policies.pastDictationsTitle,
        body: `${t.policies.pastDictationsBody} ${t.policies.deviceCategoryNote}`,
      },
    ],
  },
];

/**
 * Ce que l'organisation autorise à ses membres.
 *
 * Les valeurs affichées viennent **toujours** du serveur. Après sauvegarde,
 * c'est la réponse du serveur qui est adoptée — jamais l'écho de ce qui a été
 * envoyé : une console qui s'affiche à elle-même son propre souhait finit par
 * montrer un état que la base ne porte pas.
 *
 * Il n'y a pas d'éditeur JSON, et il n'y en aura pas. Le serveur possède le
 * schéma ; l'interface propose des contrôles typés pour les réglages qu'elle
 * connaît. Un champ libre ferait de cette page un moyen d'écrire n'importe
 * quoi dans la configuration.
 */
function Policies() {
  const { control, session } = useSession();
  const [state, retry] = useResource(
    control ? () => control.policy() : null,
    [control],
  );
  const mayManage = can(session, CAPABILITY.policyManage);

  // Les modifications en cours, séparées de ce que le serveur a renvoyé : sans
  // cette séparation, on ne saurait pas dire ce qui est enregistré et ce qui
  // ne l'est pas encore.
  const [draft, setDraft] = useState<Record<string, boolean> | null>(null);
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
    <AsyncSection state={state} onRetry={retry}>
      {(view) => {
        const settings = draft ?? view.settings;
        const dirty =
          draft !== null &&
          Object.keys(view.settings).some(
            (key) => draft[key] !== view.settings[key],
          );

        function set(key: string, value: boolean) {
          setSaved(false);
          setDraft({ ...settings, [key]: value });
        }

        async function save() {
          setBusy(true);
          setFailure(null);
          setConflict(false);
          try {
            // La révision sur laquelle cet écran a été construit. Le serveur
            // refuse si elle a bougé — il n'écrase jamais en silence.
            await control!.updatePolicy(view.revision, settings);
            setDraft(null);
            setSaved(true);
            // Re-lecture autoritative : l'écran affiche ce que le serveur
            // porte, pas ce que l'on croit avoir écrit.
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
          <Panel title={t.policies.title}>
            <p style={{ color: "var(--text-muted)" }}>{t.policies.lead}</p>
            {/* La frontière, dite à l'écran : ces réglages ne touchent jamais
                l'authentification ni l'audit. Qui administre doit pouvoir s'en
                assurer sans lire la documentation. */}
            <Notice>{t.policies.boundary}</Notice>

            <Facts
              entries={[
                [t.policies.revision, <span className="mono">{view.revision}</span>],
                [
                  t.policies.lastUpdated,
                  view.updated_at
                    ? new Date(view.updated_at * 1000).toLocaleString()
                    : t.policies.never,
                ],
              ]}
            />
            {!view.configured && <p className="policy-hint">{t.policies.defaults}</p>}

            {POLICY_GROUPS.map((group) => (
              <div key={group.title}>
                <h3 className="policy-group">{group.title}</h3>
                {group.items.map((item) => (
                  <div className="policy-setting" key={item.key}>
                    <div>
                      <strong>{item.title}</strong>
                      <p style={{ color: "var(--text-muted)", margin: 0 }}>
                        {item.body}
                      </p>
                    </div>
                    <label className="row">
                      <input
                        type="checkbox"
                        aria-label={item.title}
                        checked={settings[item.key] === true}
                        disabled={!mayManage || busy}
                        onChange={(event) => set(item.key, event.target.checked)}
                      />
                      <span>
                        {settings[item.key]
                          ? t.policies.enabled
                          : t.policies.disabled}
                      </span>
                    </label>
                  </div>
                ))}
              </div>
            ))}
            {/* Le mode pedagogique du deploiement peut fermer une fonction
                qu'aucune policy n'a fermee. Le dire evite de chercher
                longtemps pourquoi un interrupteur ouvert ne suffit pas. */}
            <p className="policy-hint">{t.policies.modeRestricted}</p>

            {!mayManage && <p className="policy-hint">{t.policies.readOnly}</p>}
            {conflict && (
              <Notice tone="danger">
                {t.policies.conflict}{" "}
                <button onClick={reload}>{t.policies.reload}</button>
              </Notice>
            )}
            {failure && <Notice tone="danger">{failure}</Notice>}
            {saved && !dirty && <Notice>{t.policies.saved}</Notice>}

            {mayManage && dirty && (
              <>
                <Notice>{t.policies.unsaved}</Notice>
                <div className="row">
                  <button disabled={busy} onClick={() => void save()}>
                    {busy ? t.policies.saving : t.policies.save}
                  </button>
                  <button disabled={busy} onClick={reload}>
                    {t.policies.discard}
                  </button>
                </div>
              </>
            )}
          </Panel>
        );
      }}
    </AsyncSection>
  );
}

/* ── Security ───────────────────────────────────────────────────────── */

export function Security() {
  const { session } = useSession();
  const admin = session.admin;

  return (
    <>
      <div className="page-head">
        <h1>{t.security.title}</h1>
        <p>{t.security.lead}</p>
      </div>

      <Panel title={t.security.yourRole}>
        <Facts
          entries={[
            [t.security.yourRole, <span className="mono">{admin?.role ?? "—"}</span>],
            [
              t.security.capabilities,
              <span className="mono">{admin?.capabilities.join(", ") ?? "—"}</span>,
            ],
            [
              t.security.sessionExpiry,
              admin ? formatDuration(secondsRemaining(admin)) : "—",
            ],
          ]}
        />
      </Panel>

      <Panel title={t.security.modernAuth}>
        <Facts
          entries={[
            [t.security.modernAuth, <Tag tone="ok">{t.security.modernAuthOn}</Tag>],
            [t.security.stepUp, t.security.stepUpDone],
          ]}
        />
        {/* Pas de note de sécurité globale : un « 92/100 » ne dit rien de ce
            qu'il faudrait corriger, et rassure à tort. */}
        <Notice>{t.security.mfaBody}</Notice>
      </Panel>

      <AuditLog />
    </>
  );
}

/**
 * Activite d'administration recente.
 *
 * Lecture seule : aucune route ne modifie ni ne supprime ce journal. Les
 * metadonnees affichees sont deja filtrees a l'ecriture — ni secret, ni jeton,
 * ni contenu dicte n'y entre.
 */
function AuditLog() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const mayRead = can(session, CAPABILITY.securityManage);
  const [pages, setPages] = useState<AuditEntry[][]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const [state, retry] = useResource(
    control && organizationId && mayRead
      ? () => control.audit(organizationId, { limit: 25 })
      : null,
    [control, organizationId, mayRead],
  );

  // La premiere page vient de la ressource ; les suivantes s'empilent.
  const entries = [...(state.data?.entries ?? []), ...pages.flat()];
  const nextBefore = cursor ?? state.data?.next_before ?? null;

  async function loadMore() {
    if (!nextBefore) return;
    setLoadingMore(true);
    try {
      const page = await control!.audit(organizationId, {
        limit: 25,
        before: nextBefore,
      });
      setPages((current) => [...current, page.entries]);
      setCursor(page.next_before);
    } finally {
      setLoadingMore(false);
    }
  }

  if (!mayRead) return null;

  return (
    <Panel title={t.security.auditTitle}>
      <p style={{ color: "var(--text-muted)" }}>{t.security.auditBody}</p>
      <AsyncSection state={state} onRetry={retry}>
        {() =>
          entries.length === 0 ? (
            <Notice>{t.security.auditEmpty}</Notice>
          ) : (
            <>
              <Facts
                entries={entries.map((entry) => [
                  new Date(entry.created_at * 1000).toLocaleString(),
                  <div>
                    <strong>{entry.action_label}</strong>
                    {" — "}
                    {/* Le jeton herite n'identifie personne : le dire vaut
                        mieux qu'afficher un acteur vide. */}
                    <span className="mono">
                      {entry.actor ?? t.security.auditLegacyActor}
                    </span>
                    {entry.target && (
                      <>
                        {" → "}
                        <span className="mono">{entry.target}</span>
                      </>
                    )}
                    {/* Ce qui a change, quand la ligne le dit : les valeurs
                        avant et apres, ou seulement les reglages modifies. */}
                    {auditChanges(entry.metadata).length > 0 && (
                      <ul className="problem-list">
                        {auditChanges(entry.metadata).map((change) => (
                          <li key={change.field}>
                            <span className="mono">{change.field}</span>
                            {(change.before !== undefined || change.after !== undefined) && (
                              <>
                                {": "}
                                {formatAuditValue(change.before)}
                                {" → "}
                                {formatAuditValue(change.after)}
                              </>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>,
                ])}
              />
              {nextBefore && (
                <div className="row">
                  <button disabled={loadingMore} onClick={() => void loadMore()}>
                    {t.security.auditMore}
                  </button>
                </div>
              )}
            </>
          )
        }
      </AsyncSection>
      <p className="hint">{t.security.auditIntegrity}</p>
    </Panel>
  );
}

/* ── Diagnostics ────────────────────────────────────────────────────── */

export function Diagnostics() {
  const { control } = useSession();
  const [health, retryHealth] = useResource(
    control ? () => control.health() : null,
    [control],
  );
  const [organization, retryOrganization] = useResource(
    control ? () => control.organization() : null,
    [control],
  );

  return (
    <>
      <div className="page-head">
        <h1>{t.diagnostics.title}</h1>
        <p>{t.diagnostics.lead}</p>
      </div>

      <Panel title={t.diagnostics.api}>
        <AsyncSection state={health} onRetry={retryHealth}>
          {(view) => (
            <Facts
              entries={[
                [t.diagnostics.api, <Tag tone="ok">{t.diagnostics.apiReachable}</Tag>],
                [t.diagnostics.version, <span className="mono">{view.version}</span>],
              ]}
            />
          )}
        </AsyncSection>
      </Panel>

      <Panel title={t.diagnostics.providers}>
        <AsyncSection state={organization} onRetry={retryOrganization}>
          {(view) => (
            <Facts
              entries={[
                [
                  t.diagnostics.discovery,
                  view.discovery.enabled ? (
                    <Tag tone="ok">{t.diagnostics.discoveryReachable}</Tag>
                  ) : (
                    <Tag>{t.identity.disabled}</Tag>
                  ),
                ],
                ...view.providers.map(
                  (provider) =>
                    [
                      provider.provider_type,
                      t.overview.enabledOf(provider.enabled_count, provider.count),
                    ] as [string, React.ReactNode],
                ),
              ]}
            />
          )}
        </AsyncSection>
      </Panel>

      {/* La frontière est rappelée à l'écran, pas seulement dans un document :
          c'est ici qu'on serait tenté d'ajouter « ce que les gens ont dicté ». */}
      <Notice>{t.diagnostics.boundary}</Notice>
    </>
  );
}
