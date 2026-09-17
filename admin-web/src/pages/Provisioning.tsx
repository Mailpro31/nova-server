/**
 * Tenant-scoped inbound provisioning.
 *
 * The generated bearer token lives only in this component state. Navigating
 * away or refreshing destroys it; no browser storage API is involved.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { ScimCredentialView } from "../lib/controlPlane";
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

function date(value: number | null): string {
  return value ? new Date(value * 1000).toLocaleString() : t.provisioning.never;
}

function Credential({
  credential,
  mayManage,
  onRevoke,
}: {
  credential: ScimCredentialView;
  mayManage: boolean;
  onRevoke: (credential: ScimCredentialView) => void;
}) {
  const usable = credential.revoked_at === null;
  return (
    <Panel
      title={credential.name}
      action={
        mayManage && usable ? (
          <button data-variant="danger" onClick={() => onRevoke(credential)}>
            {t.provisioning.revoke}
          </button>
        ) : undefined
      }
    >
      <Facts
        entries={[
          [t.provisioning.providerContext, <span className="mono">{credential.provider_context}</span>],
          [t.provisioning.created, date(credential.created_at)],
          [t.provisioning.lastUsed, date(credential.last_used_at)],
          [t.provisioning.expires, date(credential.expires_at)],
          [
            t.provisioning.status,
            usable ? (
              <Tag tone="ok">{t.provisioning.active}</Tag>
            ) : (
              <Tag tone="danger">{t.provisioning.revoked}</Tag>
            ),
          ],
        ]}
      />
    </Panel>
  );
}

export function Provisioning() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const mayManage = can(session, CAPABILITY.provisioningManage);
  const [state, retry] = useResource(
    control && organizationId
      ? () => control.provisioning(organizationId)
      : null,
    [control, organizationId],
  );
  const [name, setName] = useState("");
  const [providerContext, setProviderContext] = useState("microsoft_entra");
  const [generated, setGenerated] = useState<{ token: string; name: string } | null>(
    null,
  );
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState<ScimCredentialView | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function generate() {
    if (!control || !organizationId || !name.trim()) return;
    setBusy(true);
    setFailure(null);
    try {
      const result = await control.createScimCredential(organizationId, {
        name: name.trim(),
        provider_context: providerContext,
      });
      setGenerated({ token: result.token, name: result.credential.name });
      setName("");
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

  async function revoke() {
    if (!control || !organizationId || !revoking) return;
    setBusy(true);
    setFailure(null);
    try {
      await control.revokeScimCredential(organizationId, revoking.credential_id);
      setRevoking(null);
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

  if (revoking) {
    return (
      <ConfirmDialog
        title={t.provisioning.revokeTitle}
        body={t.provisioning.revokeBody}
        confirmLabel={t.provisioning.revokeConfirm}
        tone="danger"
        busy={busy}
        error={failure}
        onConfirm={() => void revoke()}
        onCancel={() => {
          setRevoking(null);
          setFailure(null);
        }}
      />
    );
  }

  return (
    <>
      <div className="page-head">
        <h1>{t.provisioning.title}</h1>
        <p>{t.provisioning.lead}</p>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => (
          <div className="stack">
            <Panel title={t.provisioning.status}>
              <Facts
                entries={[
                  [
                    t.provisioning.status,
                    view.status === "active" ? (
                      <Tag tone="ok">{t.provisioning.active}</Tag>
                    ) : (
                      <Tag>{t.provisioning.notConfigured}</Tag>
                    ),
                  ],
                  [t.provisioning.endpoint, <span className="mono">{view.endpoint}</span>],
                  [t.provisioning.managedUsers, String(view.users_managed)],
                  [t.provisioning.managedGroups, String(view.groups_managed)],
                  [
                    t.provisioning.lastRequest,
                    view.last_request
                      ? `${date(view.last_request.created_at)} — ${view.last_request.action}`
                      : t.provisioning.never,
                  ],
                  [
                    t.provisioning.lastSuccess,
                    view.last_success
                      ? `${date(view.last_success.created_at)} — ${view.last_success.action}`
                      : t.provisioning.never,
                  ],
                  [
                    t.provisioning.lastError,
                    view.last_error
                      ? `${date(view.last_error.created_at)} — ${view.last_error.error_code}`
                      : t.provisioning.never,
                  ],
                ]}
              />
            </Panel>

            {generated && (
              <Panel title={t.provisioning.generatedTitle}>
                <Notice>{t.provisioning.generatedBody}</Notice>
                <label>
                  {generated.name}
                  <input className="mono" readOnly value={generated.token} />
                </label>
                <div className="row">
                  <button
                    data-variant="primary"
                    onClick={() =>
                      void navigator.clipboard.writeText(generated.token).then(() =>
                        setCopied(true),
                      )
                    }
                  >
                    {t.provisioning.copy}
                  </button>
                  <button
                    data-variant="quiet"
                    onClick={() => {
                      setGenerated(null);
                      setCopied(false);
                    }}
                  >
                    {t.provisioning.done}
                  </button>
                  {copied && <Tag tone="ok">{t.provisioning.copied}</Tag>}
                </div>
              </Panel>
            )}

            <Panel title={t.provisioning.credentials}>
              {!mayManage && <Notice>{t.provisioning.readOnly}</Notice>}
              {mayManage && !generated && (
                <div className="stack">
                  <label>
                    {t.provisioning.credentialName}
                    <input
                      value={name}
                      maxLength={120}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </label>
                  <label>
                    {t.provisioning.providerContext}
                    <select
                      value={providerContext}
                      onChange={(event) => setProviderContext(event.target.value)}
                    >
                      <option value="microsoft_entra">{t.provisioning.entraProvider}</option>
                      <option value="generic">{t.provisioning.genericProvider}</option>
                      <option value="google_workspace">{t.provisioning.googleProvider}</option>
                      <option value="oidc">{t.provisioning.oidcProvider}</option>
                    </select>
                  </label>
                  {failure && <Notice tone="danger">{failure}</Notice>}
                  <div>
                    <button
                      data-variant="primary"
                      disabled={busy || !name.trim()}
                      onClick={() => void generate()}
                    >
                      {busy ? t.provisioning.generating : t.provisioning.generate}
                    </button>
                  </div>
                </div>
              )}
            </Panel>

            {view.credentials.map((credential) => (
              <Credential
                key={credential.credential_id}
                credential={credential}
                mayManage={mayManage}
                onRevoke={setRevoking}
              />
            ))}

            <Panel title={t.provisioning.entraTitle}>
              <Notice>{t.provisioning.entraWarning}</Notice>
              <ol>
                {t.provisioning.entraSteps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <Facts entries={[[t.provisioning.endpoint, <span className="mono">{view.endpoint}</span>]]} />
              <Notice>{t.provisioning.departureNote}</Notice>
            </Panel>

            <Panel title={t.provisioning.genericTitle}>
              <p>{t.provisioning.genericLead}</p>
              <ol>
                {t.provisioning.genericSteps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
              <Facts entries={[[t.provisioning.endpoint, <span className="mono">{view.endpoint}</span>]]} />
            </Panel>

            <Panel title={t.provisioning.groupsTitle}>
              {view.managed_groups.length === 0 ? (
                <p>{t.provisioning.noGroups}</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>{t.provisioning.group}</th>
                      <th>{t.provisioning.members}</th>
                      <th>{t.provisioning.updated}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.managed_groups.map((group) => (
                      <tr key={group.group_id}>
                        <td>{group.display_name}</td>
                        <td>{group.member_count}</td>
                        <td>{date(group.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Panel>
          </div>
        )}
      </AsyncSection>
    </>
  );
}
