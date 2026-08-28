/**
 * Deployment → Private Connector.
 *
 * ## Le sens de la connexion, dit à l'écran
 *
 * C'est le connector qui appelle Nova. Une DSI qui lit cette page se demande
 * d'abord ce qu'elle devra ouvrir sur son pare-feu ; la réponse — rien — ne
 * doit pas dépendre d'un document qu'elle n'a pas ouvert.
 *
 * ## Le code d'enrôlement
 *
 * Il n'existe en clair qu'une fois, dans la réponse du serveur. Il n'est ni
 * stocké, ni rechargé, ni conservé dans le navigateur : Nova n'en garde que
 * l'empreinte et ne pourrait pas le réafficher même si l'écran le demandait.
 *
 * ## Ce qui n'y entre jamais
 *
 * Aucune donnée utilisateur, et aucun credential — pas même haché.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { ConnectorEnrollmentView, ConnectorView } from "../lib/controlPlane";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Facts, Notice, Panel, Tag } from "../components/primitives";

const STATUS_TONE: Record<string, "ok" | "warn" | "danger" | undefined> = {
  online: "ok",
  degraded: "warn",
  offline: "danger",
  revoked: undefined,
};

const ROTATION_TONE: Record<string, "ok" | "warn" | "danger" | undefined> = {
  stable: "ok",
  requested: "warn",
  pending: "warn",
  failed: "danger",
};

function rotationLabel(state: string): string {
  if (state === "requested") return t.connector.rotationRequested;
  if (state === "pending") return t.connector.rotationPending;
  if (state === "failed") return t.connector.rotationFailed;
  return t.connector.rotationStable;
}

function statusLabel(status: string): string {
  if (status === "online") return t.connector.statusOnline;
  if (status === "degraded") return t.connector.statusDegraded;
  if (status === "revoked") return t.connector.statusRevoked;
  return t.connector.statusOffline;
}

function connectorFacts(connector: ConnectorView): [string, React.ReactNode][] {
  const runtime = connector.runtime_state;
  return [
    [
      t.connector.status,
      <Tag tone={STATUS_TONE[connector.status]}>{statusLabel(connector.status)}</Tag>,
    ],
    [
      t.connector.lastSeen,
      connector.last_seen_at
        ? new Date(connector.last_seen_at * 1000).toLocaleString()
        : t.connector.never,
    ],
    [t.connector.deployment, <span className="mono">{connector.deployment_id}</span>],
    [
      t.connector.connectorVersion,
      connector.connector_version ? (
        <span className="mono">{connector.connector_version}</span>
      ) : (
        t.state.notConfigured
      ),
    ],
    [
      t.connector.serverVersion,
      connector.server_version ? (
        <span className="mono">{connector.server_version}</span>
      ) : (
        t.state.notConfigured
      ),
    ],
    [t.connector.protocol, <span className="mono">v{connector.protocol_version}</span>],
    [
      t.connector.capabilities,
      connector.capabilities.length ? (
        <span className="mono">{connector.capabilities.join(", ")}</span>
      ) : (
        "—"
      ),
    ],
    [
      t.connector.runtimeStatus,
      runtime?.runtime_status ? (
        <span className="mono">{runtime.runtime_status}</span>
      ) : (
        t.state.notConfigured
      ),
    ],
    // Livré et appliqué restent deux lignes. Les fondre ferait croire qu'une
    // révision partie est une révision posée.
    [
      t.connector.deliveredRevision,
      <span className="mono">{connector.delivered_revision}</span>,
    ],
    [
      t.connector.appliedRevision,
      <span className="mono">{connector.applied_revision}</span>,
    ],
    [t.connector.applyStatus, <span className="mono">{connector.apply_status}</span>],
    [
      t.connector.lastError,
      connector.last_error_code ? (
        <span className="mono">{connector.last_error_code}</span>
      ) : (
        "—"
      ),
    ],
  ];
}

export function PrivateConnector() {
  const { control, session } = useSession();
  const [state, retry] = useResource(control ? () => control.connectors() : null, [control]);
  const mayManage = can(session, CAPABILITY.deploymentManage);

  const [displayName, setDisplayName] = useState("");
  const [deploymentId, setDeploymentId] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [rotating, setRotating] = useState<string | null>(null);
  // Le code vit dans l'état de ce composant, le temps de l'afficher. Il ne va
  // ni dans le stockage du navigateur, ni dans l'URL.
  const [issued, setIssued] = useState<ConnectorEnrollmentView | null>(null);

  async function createEnrollment() {
    setBusy(true);
    setFailure(null);
    try {
      const created = await control!.createConnectorEnrollment({
        display_name: displayName.trim(),
        deployment_id: deploymentId.trim(),
      });
      setIssued(created);
      setDisplayName("");
      setDeploymentId("");
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

  async function requestRotation(connectorId: string) {
    setRotating(connectorId);
    setFailure(null);
    try {
      await control!.requestConnectorRotation(connectorId);
      // Re-lecture autoritative : l'ecran montre l'etat que le serveur porte,
      // jamais l'echo de la demande. « Demande » n'est pas « fait ».
      retry();
    } catch (raised) {
      setFailure(
        raised instanceof ApiError
          ? errorMessage(raised.kind, raised.code)
          : errorMessage("server"),
      );
    } finally {
      setRotating(null);
    }
  }

  async function revoke(connectorId: string) {
    setBusy(true);
    setFailure(null);
    try {
      await control!.revokeConnector(connectorId);
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
    <AsyncSection state={state} onRetry={retry}>
      {(view) => (
        <>
          <Panel title={t.connector.title}>
            <p style={{ color: "var(--text-muted)" }}>{t.connector.lead}</p>
            {/* Les deux questions que pose une DSI devant cette page : ce qui
                circule, et ce qu'elle devra ouvrir. */}
            <Notice>{t.connector.privacy}</Notice>
            <p className="policy-hint">{t.connector.outbound}</p>
            {view.connectors.length === 0 && <Notice>{t.connector.none}</Notice>}
            {view.pending_enrollments > 0 && (
              <p className="policy-hint">
                {view.pending_enrollments} {t.connector.pending}
              </p>
            )}
          </Panel>

          {view.connectors.map((connector) => (
            <Panel key={connector.connector_id} title={connector.display_name}>
              <Facts entries={connectorFacts(connector)} />
              {/* Un protocole trop ancien se dit, sans promettre une mise à
                  jour automatique que Nova ne sait pas encore faire. */}
              {!connector.protocol_supported && (
                <Notice tone="danger">{t.connector.protocolUnsupported}</Notice>
              )}
              {connector.protocol_supported &&
                connector.protocol_version < view.protocol_version && (
                  <Notice>{t.connector.updateRecommended}</Notice>
                )}
              {/* Le credential : son age, sa generation, l'etat de sa
                  bascule. Jamais le secret, jamais son empreinte. */}
              <h3 className="policy-group">{t.connector.credentialTitle}</h3>
              <Facts
                entries={[
                  [
                    t.connector.credentialAge,
                    `${connector.credential.age_days} j`,
                  ],
                  [
                    t.connector.credentialGeneration,
                    <span className="mono">{connector.credential.generation}</span>,
                  ],
                  [
                    t.connector.lastRotated,
                    connector.credential.last_rotated_at
                      ? new Date(
                          connector.credential.last_rotated_at * 1000,
                        ).toLocaleString()
                      : t.connector.neverRotated,
                  ],
                  [
                    t.connector.rotationState,
                    <Tag tone={ROTATION_TONE[connector.credential.rotation_state]}>
                      {rotationLabel(connector.credential.rotation_state)}
                    </Tag>,
                  ],
                ]}
              />
              {connector.credential.rotation_state === "failed" && (
                <Notice tone="danger">{t.connector.rotationFailed}</Notice>
              )}
              {!connector.credential.rotation_supported && (
                <Notice>{t.connector.rotationUnsupported}</Notice>
              )}
              {mayManage &&
                connector.status !== "revoked" &&
                connector.credential.rotation_supported && (
                  <>
                    <p className="policy-hint">{t.connector.rotationBody}</p>
                    {/* Rotation et revocation ne repondent pas a la meme
                        question. Le dire ici evite de choisir la mauvaise le
                        jour ou cela compte. */}
                    <p className="policy-hint">{t.connector.rotationCompromised}</p>
                    <div className="row">
                      <button
                        disabled={
                          busy ||
                          rotating === connector.connector_id ||
                          connector.credential.rotation_state === "requested" ||
                          connector.credential.rotation_state === "pending"
                        }
                        onClick={() => void requestRotation(connector.connector_id)}
                      >
                        {rotating === connector.connector_id
                          ? t.connector.requestingRotation
                          : t.connector.requestRotation}
                      </button>
                    </div>
                  </>
                )}
              {mayManage && connector.status !== "revoked" && (
                <>
                  <p className="policy-hint">{t.connector.revokeBody}</p>
                  <div className="row">
                    <button
                      disabled={busy}
                      onClick={() => void revoke(connector.connector_id)}
                    >
                      {busy ? t.connector.revoking : t.connector.revoke}
                    </button>
                  </div>
                </>
              )}
            </Panel>
          ))}

          <Panel title={t.connector.enrollTitle}>
            <p style={{ color: "var(--text-muted)" }}>{t.connector.enrollBody}</p>
            {!mayManage && <p className="policy-hint">{t.connector.readOnly}</p>}
            {failure && <Notice tone="danger">{failure}</Notice>}

            {mayManage && !issued && (
              <>
                <div className="policy-setting">
                  <label htmlFor="connector-name">{t.connector.enrollName}</label>
                  <input
                    id="connector-name"
                    value={displayName}
                    disabled={busy}
                    onChange={(event) => setDisplayName(event.target.value)}
                  />
                </div>
                <div className="policy-setting">
                  <label htmlFor="connector-deployment">
                    {t.connector.enrollDeployment}
                  </label>
                  <input
                    id="connector-deployment"
                    value={deploymentId}
                    disabled={busy}
                    onChange={(event) => setDeploymentId(event.target.value)}
                  />
                </div>
                <div className="row">
                  <button
                    disabled={busy || !displayName.trim() || !deploymentId.trim()}
                    onClick={() => void createEnrollment()}
                  >
                    {busy ? t.connector.enrollCreating : t.connector.enrollAction}
                  </button>
                </div>
              </>
            )}

            {issued && (
              <>
                {/* Une seule fois, et l'écran le dit avant que la personne ne
                    ferme l'onglet. */}
                <Notice tone="danger">{t.connector.enrollOnce}</Notice>
                <Facts
                  entries={[
                    [
                      t.connector.enrollExpires,
                      `${Math.round(issued.expires_in / 60)} min`,
                    ],
                    [
                      t.connector.enrollCommand,
                      <code className="mono">
                        {`nova-connector enroll --control-plane ${window.location.origin} --token ${issued.enrollment_token}`}
                      </code>,
                    ],
                  ]}
                />
                <div className="row">
                  <button onClick={() => setIssued(null)}>{t.connector.enrollDone}</button>
                </div>
              </>
            )}
          </Panel>
        </>
      )}
    </AsyncSection>
  );
}
