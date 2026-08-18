/**
 * Vue d'ensemble.
 *
 * Ce qui **n'y est pas** compte autant que ce qui y est : aucun nombre
 * d'utilisateurs, aucun taux de disponibilité, aucune « santé » synthétique. Le
 * serveur ne les fournit pas, et une console d'administration qui invente des
 * chiffres devient impossible à croire sur les autres.
 */

import { AsyncSection, Facts, Panel, Tag } from "../components/primitives";
import { t } from "../lib/messages";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";

export function Overview() {
  const { control, session } = useSession();
  const [state, retry] = useResource(
    control ? () => control.organization() : null,
    [control],
  );

  return (
    <>
      <div className="page-head">
        <h1>{t.overview.title}</h1>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => (
          <>
            <Panel title={t.overview.organization}>
              <Facts
                entries={[
                  [t.overview.organization, view.organization.display_name],
                  [
                    t.overview.status,
                    <Tag tone={view.organization.status === "active" ? "ok" : "warn"}>
                      {view.organization.status}
                    </Tag>,
                  ],
                  [t.overview.serverMode, view.deployment.server_mode],
                  [t.overview.deploymentMode, view.deployment.deployment_mode],
                  [
                    t.overview.discovery,
                    view.discovery.enabled ? (
                      <Tag tone="ok">{t.identity.enabled}</Tag>
                    ) : (
                      <Tag>{t.identity.disabled}</Tag>
                    ),
                  ],
                ]}
              />
            </Panel>

            <Panel title={t.overview.providers}>
              {view.providers.length === 0 ? (
                <p className="notice">{t.state.notConfigured}</p>
              ) : (
                <Facts
                  entries={view.providers.map((provider) => [
                    provider.provider_type,
                    t.overview.enabledOf(provider.enabled_count, provider.count),
                  ])}
                />
              )}
            </Panel>

            <Panel title={t.overview.yourAccess}>
              <Facts
                entries={[
                  [t.security.yourRole, session.admin?.role ?? "—"],
                  [
                    t.security.capabilities,
                    <span className="mono">
                      {session.admin?.capabilities.join(", ") ?? "—"}
                    </span>,
                  ],
                ]}
              />
            </Panel>
          </>
        )}
      </AsyncSection>
    </>
  );
}
