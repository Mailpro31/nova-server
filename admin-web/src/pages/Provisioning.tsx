/**
 * Tenant-scoped inbound provisioning.
 *
 * The generated bearer token lives only in this component state. Navigating
 * away or refreshing destroys it; no browser storage API is involved.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { DirectorySyncBody, ScimCredentialView } from "../lib/controlPlane";
import { errorMessage, t } from "../lib/messages";

/** Une adresse de groupe par ligne. */
const LINE_BREAK = "\n";
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

/**
 * Google Workspace : ce que Nova lit, et quand.
 *
 * Le formulaire ne demande jamais la clé du compte de service — seulement la
 * référence sous laquelle le serveur la trouve. Un champ « collez votre clé »
 * l'enverrait droit en base, puis dans la prochaine sauvegarde.
 */
function GoogleDirectorySync({
  organizationId,
  mayManage,
}: {
  organizationId: string;
  mayManage: boolean;
}) {
  const { control, session } = useSession();
  const mayConfigure = mayManage && can(session, CAPABILITY.identityManage);
  const [state, retry] = useResource(
    control && organizationId ? () => control.directorySync(organizationId) : null,
    [control, organizationId],
  );
  const [draft, setDraft] = useState<DirectorySyncBody | null>(null);
  const [groupsText, setGroupsText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
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
    <Panel title={t.googleSync.title}>
      <p>{t.googleSync.lead}</p>
      <ol>
        {t.googleSync.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>

      <AsyncSection state={state} onRetry={retry}>
        {(view) => {
          const current: DirectorySyncBody = draft ?? {
            enabled: view.sync.enabled,
            admin_subject: view.sync.admin_subject,
            customer_id: view.sync.customer_id || "my_customer",
            credentials_ref: "",
            group_filter: view.sync.group_filter,
          };
          const groups =
            draft === null ? view.sync.group_filter.join(LINE_BREAK) : groupsText;
          return (
            <>
              <Facts
                entries={[
                  [
                    t.googleSync.lastRun,
                    view.sync.last_sync_at
                      ? new Date(view.sync.last_sync_at * 1000).toLocaleString()
                      : t.googleSync.never,
                  ],
                  [
                    t.googleSync.statusOk,
                    view.sync.last_status === "ok" ? (
                      <Tag tone="ok">{t.googleSync.statusOk}</Tag>
                    ) : view.sync.last_status === "error" ? (
                      <Tag tone="warn">
                        {view.sync.last_error
                          ? errorMessage("server", view.sync.last_error)
                          : t.googleSync.statusError}
                      </Tag>
                    ) : (
                      "\u2014"
                    ),
                  ],
                ]}
              />
              {view.sync.last_outcome && (
                <p className="policy-hint">
                  {t.googleSync.outcome(
                    view.sync.last_outcome.groups,
                    view.sync.last_outcome.added,
                    view.sync.last_outcome.removed,
                  )}
                </p>
              )}
              <p className="policy-hint">{t.googleSync.readOnlyGroups}</p>
              <p className="policy-hint">{t.googleSync.hourly}</p>

              {mayConfigure && (
                <div className="stack">
                  <label className="policy-setting">
                    <span>{t.googleSync.enabled}</span>
                    <input
                      type="checkbox"
                      checked={current.enabled}
                      disabled={busy}
                      onChange={(event) => {
                        setGroupsText(groups);
                        setDraft({ ...current, enabled: event.target.checked });
                      }}
                    />
                  </label>
                  <label className="field">
                    <span>{t.googleSync.adminSubject}</span>
                    <input
                      type="email"
                      value={current.admin_subject}
                      disabled={busy}
                      placeholder="admin@example.com"
                      onChange={(event) => {
                        setGroupsText(groups);
                        setDraft({ ...current, admin_subject: event.target.value });
                      }}
                    />
                  </label>
                  <p className="policy-hint">{t.googleSync.adminSubjectHint}</p>
                  <label className="field">
                    <span>{t.googleSync.keyReference}</span>
                    <input
                      type="text"
                      value={current.credentials_ref}
                      disabled={busy}
                      placeholder="env://NOVA_GOOGLE_SERVICE_ACCOUNT_KEY"
                      onChange={(event) => {
                        setGroupsText(groups);
                        setDraft({ ...current, credentials_ref: event.target.value });
                      }}
                    />
                  </label>
                  <p className="policy-hint">{t.googleSync.keyHint}</p>
                  <label className="field">
                    <span>{t.googleSync.groupFilter}</span>
                    <textarea
                      rows={4}
                      value={groups}
                      disabled={busy}
                      onChange={(event) => {
                        setGroupsText(event.target.value);
                        setDraft({ ...current });
                      }}
                    />
                  </label>
                  <p className="policy-hint">{t.googleSync.groupFilterHint}</p>
                  {notice && <Notice>{notice}</Notice>}
                  <div className="row">
                    <button
                      data-variant="primary"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const body: DirectorySyncBody = {
                            ...current,
                            group_filter: groups
                              .split(LINE_BREAK)
                              .map((line) => line.trim())
                              .filter((line) => line.length > 0),
                          };
                          await control!.setDirectorySync(organizationId, body);
                          setDraft(null);
                          setNotice(t.googleSync.saved);
                        })
                      }
                    >
                      {t.googleSync.save}
                    </button>
                    <button
                      disabled={busy || !view.sync.enabled}
                      onClick={() =>
                        void run(async () => {
                          const result = await control!.runDirectorySync(organizationId);
                          setNotice(
                            t.googleSync.outcome(
                              result.outcome.groups,
                              result.outcome.added,
                              result.outcome.removed,
                            ),
                          );
                        })
                      }
                    >
                      {busy ? t.googleSync.running : t.googleSync.runNow}
                    </button>
                  </div>
                </div>
              )}
              {failure && <Notice tone="danger">{failure}</Notice>}
            </>
          );
        }}
      </AsyncSection>
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

            <GoogleDirectorySync organizationId={organizationId} mayManage={mayManage} />

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
