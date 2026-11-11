/**
 * Accueil de la console (refonte du 11/11, demande de Sash).
 *
 * D'abord « est-ce que tout marche ? », puis les chiffres que le serveur
 * fournit déjà, les alertes qui demandent une action et les tâches courantes.
 * Aucun chiffre n'est inventé : chaque bloc vient d'une route du serveur, et un
 * bloc que le rôle ne peut pas lire ne s'affiche simplement pas. Les réglages
 * techniques d'avant restent là, repliés.
 */

import { Link } from "react-router-dom";
import { AsyncSection, Facts, Panel, Tag } from "../components/primitives";
import { engineSummary, HOME_SHORTCUTS } from "../lib/home";
import { t } from "../lib/messages";
import { subscriptionAlerts } from "../lib/subscription";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { alertText } from "./Subscription";

const TONE = { ok: "ok", warn: "warn", down: "danger" } as const;

export function Overview() {
  const { control, session } = useSession();
  const [state, retry] = useResource(
    control ? () => control.organization() : null,
    [control],
  );
  const [runtime] = useResource(control ? () => control.aiRuntime() : null, [control]);
  const [subscription] = useResource(control ? () => control.subscription() : null, [control]);
  const [adoption] = useResource(control ? () => control.adoption() : null, [control]);
  const [usage] = useResource(control ? () => control.usageMetrics(24) : null, [control]);

  const engine =
    runtime.data ? engineSummary(runtime.data.reported.runtime_status) : null;
  const alerts =
    subscription.data
      ? subscriptionAlerts(subscription.data).map((alert) => alertText(alert, subscription.data!))
      : [];
  const figures: [string, string][] = [];
  if (subscription.data)
    figures.push([t.home.activeMonth, String(subscription.data.active_month)]);
  if (usage.data)
    figures.push([t.home.dictationsToday, String(usage.data.summary.dictations)]);
  if (adoption.data)
    figures.push([
      t.home.versions,
      adoption.data.versions.length === 0
        ? t.home.noVersion
        : adoption.data.versions.map((v) => `${v.version} (${v.people})`).join(", "),
    ]);

  return (
    <>
      <div className="page-head">
        <h1>{t.overview.title}</h1>
      </div>

      {engine && (
        <div className="home-status" data-tone={engine} role="status">
          <Tag tone={TONE[engine]}>{engine === "ok" ? "✓" : "!"}</Tag>
          <strong>{engine === "ok" ? t.home.allGood : engine === "warn" ? t.home.warn : t.home.down}</strong>
          <Link to="../diagnostics">{t.home.engineDetails}</Link>
        </div>
      )}

      {figures.length > 0 && (
        <Panel title={t.home.keyFigures}>
          <Facts entries={figures} />
        </Panel>
      )}

      {subscription.data && (
        <Panel title={t.home.alertsTitle}>
          {alerts.length === 0 ? (
            <p>{t.home.noAlert}</p>
          ) : (
            <ul className="home-alerts">
              {alerts.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <Panel title={t.home.shortcutsTitle}>
        <nav className="home-shortcuts" aria-label={t.home.shortcutsTitle}>
          {HOME_SHORTCUTS.map((shortcut) => (
            <Link key={shortcut.path} to={`../${shortcut.path}`} className="home-shortcut">
              {t.home.shortcuts[shortcut.key]}
            </Link>
          ))}
        </nav>
      </Panel>

      <details className="home-technical">
        <summary>{t.home.technical}</summary>
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

            <Panel title={t.overview.seats}>
              {/* Compteur reel : c'est lui qui refuse une connexion de plus
                  quand le plafond est atteint. */}
              <Facts
                entries={[
                  [
                    t.overview.seats,
                    `${view.seats.used} / ${view.seats.limit}`,
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
      </details>
    </>
  );
}
