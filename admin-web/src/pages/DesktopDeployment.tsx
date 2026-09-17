/**
 * Déploiement Windows du poste Nova.
 *
 * Cette page ne fabrique rien. Quand le service ne connaît pas de release, elle
 * le dit ; quand aucun poste ne rapporte son état, elle le dit aussi. Une
 * console de déploiement qui afficherait un parc « en bonne santé » qu'elle n'a
 * pas mesuré serait pire qu'une console vide.
 *
 * La commande d'installation affichée ne porte aucun secret durable : un
 * identifiant de parc et une organisation, tous deux opaques. Quelqu'un qui la
 * lit par-dessus une épaule ne peut ni se connecter, ni administrer.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { DeploymentProfileView, DeploymentView } from "../lib/controlPlane";
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

const CHANNEL_LABEL: Record<string, string> = {
  stable: t.deployment.channelStable,
  preview: t.deployment.channelPreview,
};

const POLICY_LABEL: Record<string, string> = {
  manual: t.deployment.policyManual,
  notify: t.deployment.policyNotify,
  automatic: t.deployment.policyAutomatic,
};

const STATUS_LABEL: Record<DeploymentView["status"], string> = {
  not_configured: t.deployment.statusNotConfigured,
  package_ready: t.deployment.statusPackageReady,
  configuration_error: t.deployment.statusConfigurationError,
};

function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="stack">
      <input className="mono" readOnly value={value} />
      <div className="row">
        <button
          data-variant="primary"
          onClick={() => {
            void navigator.clipboard.writeText(value).then(() => setCopied(true));
          }}
        >
          {t.deployment.copy}
        </button>
        {copied && <Tag tone="ok">{t.deployment.copied}</Tag>}
      </div>
    </div>
  );
}

function Profile({
  profile,
  command,
  mayManage,
  busy,
  onRetire,
  onChange,
}: {
  profile: DeploymentProfileView;
  command: string | undefined;
  mayManage: boolean;
  busy: boolean;
  onRetire: (profile: DeploymentProfileView) => void;
  onChange: (
    profile: DeploymentProfileView,
    body: { channel?: string; update_policy?: string; status?: string },
  ) => Promise<boolean>;
}) {
  const active = profile.status === "active";
  const [editing, setEditing] = useState(false);
  const [channel, setChannel] = useState<string>(profile.channel);
  const [policy, setPolicy] = useState<string>(profile.update_policy);
  return (
    <Panel
      title={profile.name}
      action={
        mayManage ? (
          active ? (
            <button data-variant="danger" disabled={busy} onClick={() => onRetire(profile)}>
              {t.deployment.retire}
            </button>
          ) : (
            <button
              data-variant="primary"
              disabled={busy}
              onClick={() => void onChange(profile, { status: "active" })}
            >
              {t.deployment.reactivate}
            </button>
          )
        ) : undefined
      }
    >
      <Facts
        entries={[
          [
            t.deployment.profileStatus,
            active ? (
              <Tag tone="ok">{t.deployment.profileActive}</Tag>
            ) : (
              <Tag tone="danger">{t.deployment.profileRetired}</Tag>
            ),
          ],
          [t.deployment.channel, CHANNEL_LABEL[profile.channel] ?? profile.channel],
          [
            t.deployment.updatePolicy,
            POLICY_LABEL[profile.update_policy] ?? profile.update_policy,
          ],
          [t.deployment.revision, String(profile.config_revision)],
        ]}
      />
      {mayManage &&
        (editing ? (
          <div className="stack">
            <label>
              {t.deployment.channel}
              <select value={channel} onChange={(event) => setChannel(event.target.value)}>
                <option value="stable">{t.deployment.channelStable}</option>
                <option value="preview">{t.deployment.channelPreview}</option>
              </select>
            </label>
            <label>
              {t.deployment.updatePolicy}
              <select value={policy} onChange={(event) => setPolicy(event.target.value)}>
                <option value="manual">{t.deployment.policyManual}</option>
                <option value="notify">{t.deployment.policyNotify}</option>
                <option value="automatic">{t.deployment.policyAutomatic}</option>
              </select>
            </label>
            <div className="row">
              <button
                data-variant="primary"
                disabled={busy}
                onClick={() =>
                  void onChange(profile, { channel, update_policy: policy }).then((done) => {
                    if (done) setEditing(false);
                  })
                }
              >
                {t.deployment.save}
              </button>
              <button data-variant="quiet" disabled={busy} onClick={() => setEditing(false)}>
                {t.deployment.cancel}
              </button>
            </div>
          </div>
        ) : (
          <div className="row">
            <button disabled={busy} onClick={() => setEditing(true)}>
              {t.deployment.edit}
            </button>
          </div>
        ))}
      {active && command ? (
        <>
          <h3>{t.deployment.installCommand}</h3>
          <CopyField value={command} />
          <Notice>{t.deployment.installCommandNote}</Notice>
        </>
      ) : null}
    </Panel>
  );
}

export function DesktopDeployment() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const mayManage = can(session, CAPABILITY.deploymentManage);
  const [state, retry] = useResource(
    control && organizationId ? () => control.deployment(organizationId) : null,
    [control, organizationId],
  );
  const [name, setName] = useState("");
  const [channel, setChannel] = useState("stable");
  const [policy, setPolicy] = useState("notify");
  const [retiring, setRetiring] = useState<DeploymentProfileView | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function create() {
    if (!control || !organizationId || !name.trim()) return;
    setBusy(true);
    setFailure(null);
    try {
      await control.createDeploymentProfile(organizationId, {
        name: name.trim(),
        channel,
        update_policy: policy,
      });
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

  /** Réactiver, changer de canal ou de politique : une écriture, puis relecture. */
  async function change(
    profile: DeploymentProfileView,
    body: { channel?: string; update_policy?: string; status?: string },
  ): Promise<boolean> {
    if (!control || !organizationId) return false;
    setBusy(true);
    setFailure(null);
    try {
      await control.updateDeploymentProfile(organizationId, profile.profile_id, body);
      retry();
      return true;
    } catch (raised) {
      setFailure(
        raised instanceof ApiError
          ? errorMessage(raised.kind, raised.code)
          : errorMessage("server"),
      );
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function retire() {
    if (!control || !organizationId || !retiring) return;
    setBusy(true);
    setFailure(null);
    try {
      await control.updateDeploymentProfile(organizationId, retiring.profile_id, {
        status: "retired",
      });
      setRetiring(null);
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
      {(view) => {
        const release = view.release.channels.stable ?? null;
        return (
          <>
            <Panel title={t.deployment.desktopTitle}>
              <p>{t.deployment.desktopLead}</p>
              <Facts
                entries={[
                  [t.deployment.packageStatus, STATUS_LABEL[view.status]],
                  [t.deployment.installer, <span className="mono">{view.installer_filename}</span>],
                  [
                    t.deployment.origin,
                    <span className="mono">{view.control_plane_origin}</span>,
                  ],
                  [
                    t.deployment.deviceReporting,
                    view.device_reporting ? null : t.deployment.deviceReportingNone,
                  ],
                ]}
              />
              <Notice>{t.deployment.originNote}</Notice>
            </Panel>

            <Panel title={t.deployment.releaseTitle}>
              {view.release.error ? (
                <Notice tone="danger">{t.deployment.releaseError}</Notice>
              ) : null}
              {!view.release.configured || !release ? (
                <Notice>{t.deployment.releaseNone}</Notice>
              ) : (
                <>
                  <Facts
                    entries={[
                      [t.deployment.desktopVersion, <span className="mono">{release.version}</span>],
                      [
                        t.deployment.minimumVersion,
                        <span className="mono">{release.minimum_supported_version}</span>,
                      ],
                      [t.deployment.artifact, <span className="mono">{release.artifact}</span>],
                      [t.deployment.digest, <span className="mono">{release.sha256}</span>],
                      [
                        t.deployment.signed,
                        view.release.signed ? (
                          <Tag tone="ok">{t.deployment.signedYes}</Tag>
                        ) : (
                          <Tag tone="warn">{t.deployment.signedNo}</Tag>
                        ),
                      ],
                    ]}
                  />
                  {view.release.signed ? null : (
                    <Notice>{t.deployment.releaseUnsigned}</Notice>
                  )}
                </>
              )}
            </Panel>

            <Panel title={t.deployment.profilesTitle}>
              {!mayManage ? <Notice>{t.deployment.readOnly}</Notice> : null}
              {mayManage ? (
                <div className="stack">
                  <label>
                    {t.deployment.profileName}
                    <input
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </label>
                  <label>
                    {t.deployment.channel}
                    <select
                      value={channel}
                      onChange={(event) => setChannel(event.target.value)}
                    >
                      <option value="stable">{t.deployment.channelStable}</option>
                      <option value="preview">{t.deployment.channelPreview}</option>
                    </select>
                  </label>
                  <label>
                    {t.deployment.updatePolicy}
                    <select
                      value={policy}
                      onChange={(event) => setPolicy(event.target.value)}
                    >
                      <option value="manual">{t.deployment.policyManual}</option>
                      <option value="notify">{t.deployment.policyNotify}</option>
                      <option value="automatic">{t.deployment.policyAutomatic}</option>
                    </select>
                  </label>
                  <button disabled={busy || !name.trim()} onClick={() => void create()}>
                    {busy ? t.deployment.creating : t.deployment.create}
                  </button>
                </div>
              ) : null}
              {failure ? <Notice tone="danger">{failure}</Notice> : null}
              {view.profiles.length === 0 ? (
                <Notice>{t.deployment.noProfiles}</Notice>
              ) : null}
            </Panel>

            {view.profiles.map((profile) => (
              <Profile
                key={profile.profile_id}
                profile={profile}
                command={view.install_commands[profile.profile_id]}
                mayManage={mayManage}
                busy={busy}
                onRetire={setRetiring}
                onChange={change}
              />
            ))}

            {retiring ? (
              <ConfirmDialog
                title={t.deployment.retireTitle}
                body={t.deployment.retireBody}
                confirmLabel={t.deployment.retireConfirm}
                tone="danger"
                busy={busy}
                error={failure}
                onCancel={() => setRetiring(null)}
                onConfirm={() => void retire()}
              />
            ) : null}
          </>
        );
      }}
    </AsyncSection>
  );
}
