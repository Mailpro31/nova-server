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
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can, secondsRemaining } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Facts, Notice, Panel, Tag } from "../components/primitives";

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
              {/* Ni parc de postes, ni canaux de mise à jour : le serveur n'en
                  sait rien, et une page qui les inventerait serait un mensonge
                  poli. */}
              <Notice>{t.deployment.fleetBody}</Notice>
            </Panel>
          </>
        )}
      </AsyncSection>
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
    </>
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

      <Panel title={t.security.auditTitle}>
        <Notice>{t.security.auditBody}</Notice>
      </Panel>
    </>
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
