/**
 * Gestion des membres de l'organisation.
 *
 * ## La frontière, tenue dans le code
 *
 * Cet écran montre des identités et des accès. Il ne montre **jamais** ce que
 * les gens dictent : ni audio, ni transcription, ni prompt, ni presse-papiers,
 * ni « activité ». Administrer des accès n'est pas surveiller des personnes, et
 * c'est ici que la tentation serait la plus forte — un onglet de plus, et la
 * console devient un outil de surveillance.
 *
 * La frontière est rappelée à l'écran, pas seulement dans un document.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { MemberDetail, MemberView } from "../lib/controlPlane";
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

const MEMBER_ROLES = ["student", "teacher", "staff", "partner"] as const;

function timestamp(value: number | null): string {
  return value ? new Date(value * 1000).toLocaleString() : "—";
}

/** Fiche d'un membre : identité, appartenance, appareils, actions. */
/** Ce que cette personne voit de ses groupes. Son choix l'emporte sur le reste. */
function MemberGroupDisplayPanel({
  organizationId,
  userId,
}: {
  organizationId: string;
  userId: string;
}) {
  const { control } = useSession();
  const [state, retry] = useResource(
    control ? () => control.memberGroupDisplay(organizationId, userId) : null,
    [control, organizationId, userId],
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function choose(value: string) {
    const visible = value === "show" ? true : value === "hide" ? false : null;
    setBusy(true);
    setFailure(null);
    try {
      await control!.setMemberGroupDisplay(organizationId, userId, visible);
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
    <Panel title={t.members.groupDisplayTitle}>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => (
          <>
            <label htmlFor="member-group-display">
              {t.members.groupDisplayTitle}
              <select
                id="member-group-display"
                disabled={busy}
                value={view.visible === null ? "inherit" : view.visible ? "show" : "hide"}
                onChange={(event) => void choose(event.target.value)}
              >
                <option value="inherit">{t.members.groupDisplayInherit}</option>
                <option value="show">{t.members.groupDisplayShow}</option>
                <option value="hide">{t.members.groupDisplayHide}</option>
              </select>
            </label>
            <p className="policy-hint">
              {t.members.groupDisplayEffective(view.effective)} {t.members.groupDisplayHint}
            </p>
          </>
        )}
      </AsyncSection>
      {failure && <Notice tone="danger">{failure}</Notice>}
    </Panel>
  );
}

function MemberDetailView({
  userId,
  onClose,
  onChanged,
}: {
  userId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const mayManage = can(session, CAPABILITY.identityManage);
  const [state, retry] = useResource<MemberDetail>(
    control ? () => control.member(organizationId, userId) : null,
    [control, organizationId, userId],
  );
  const [pending, setPending] = useState<
    "suspend" | "reactivate" | "revoke" | "delete" | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [profile, setProfile] = useState<{ role?: string; cohort?: string } | null>(
    null,
  );

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      setPending(null);
      after?.();
      retry();
      onChanged();
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

  if (pending) {
    const dialogs = {
      suspend: {
        title: t.members.suspendTitle,
        body: t.members.suspendBody,
        confirm: t.members.suspendConfirm,
        tone: "danger" as const,
        run: () => control!.setMemberStatus(organizationId, userId, "disabled"),
      },
      reactivate: {
        title: t.members.reactivateTitle,
        body: t.members.reactivateBody,
        confirm: t.members.reactivateConfirm,
        tone: undefined,
        run: () => control!.setMemberStatus(organizationId, userId, "active"),
      },
      revoke: {
        title: t.members.revokeTitle,
        body: t.members.revokeBody,
        confirm: t.members.revokeConfirm,
        tone: undefined,
        run: async () => {
          const result = await control!.revokeMemberSessions(organizationId, userId);
          setNotice(t.members.revoked(result.revoked_devices));
        },
      },
      delete: {
        title: t.members.removeTitle,
        body: t.members.removeBody,
        confirm: t.members.removeConfirm,
        tone: "danger" as const,
        run: async () => {
          await control!.deleteMember(organizationId, userId);
          onClose();
        },
      },
    };
    const dialog = dialogs[pending];
    return (
      <ConfirmDialog
        title={dialog.title}
        body={dialog.body}
        confirmLabel={dialog.confirm}
        tone={dialog.tone}
        busy={busy}
        error={failure}
        onConfirm={() => void run(dialog.run)}
        onCancel={() => {
          setPending(null);
          setFailure(null);
        }}
      />
    );
  }

  return (
    <>
      <div className="page-head">
        <button data-variant="quiet" onClick={onClose}>
          ← {t.members.back}
        </button>
      </div>
      <AsyncSection state={state} onRetry={retry}>
        {(detail) => {
          const member = detail.member;
          const suspended = member.status !== "active";
          const role = profile?.role ?? member.member_role;
          const cohort = profile?.cohort ?? (member.groups?.[0]?.label ?? "");
          return (
            <>
              <Panel title={member.email}>
                <Facts
                  entries={[
                    [
                      t.identity.status,
                      <Tag tone={suspended ? "warn" : "ok"}>{member.status}</Tag>,
                    ],
                    [t.members.memberType, member.member_type ?? "—"],
                    [t.security.yourRole, <span className="mono">{member.security_role}</span>],
                    [
                      t.members.groups,
                      member.groups?.length
                        ? member.groups.map((group) => group.label).join(", ")
                        : t.members.noGroup,
                    ],
                    [t.members.adminSessions, String(detail.open_admin_sessions)],
                  ]}
                />
                {notice && <Notice>{notice}</Notice>}
                {failure && <Notice tone="danger">{failure}</Notice>}
              </Panel>

              {mayManage && (
                <Panel title={t.members.memberType}>
                  <label htmlFor="member-role">
                    {t.members.memberType}
                    <select
                      id="member-role"
                      value={role}
                      onChange={(event) =>
                        setProfile({ role: event.target.value, cohort })
                      }
                    >
                      {MEMBER_ROLES.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label htmlFor="member-cohort">
                    {t.members.groups}
                    <input
                      id="member-cohort"
                      type="text"
                      value={cohort}
                      onChange={(event) =>
                        setProfile({ role, cohort: event.target.value })
                      }
                    />
                  </label>
                  <div className="row">
                    <button
                      data-variant="primary"
                      disabled={busy || profile === null}
                      onClick={() =>
                        void run(
                          () =>
                            control!.updateMemberProfile(organizationId, userId, {
                              member_role: role,
                              cohort,
                            }),
                          () => {
                            setProfile(null);
                            setNotice(t.members.profileSaved);
                          },
                        )
                      }
                    >
                      {t.members.saveProfile}
                    </button>
                  </div>
                </Panel>
              )}

              {mayManage && (
                <MemberGroupDisplayPanel organizationId={organizationId} userId={userId} />
              )}

              <Panel title={t.members.devices}>
                {detail.devices.length === 0 ? (
                  <p>{t.members.noDevice}</p>
                ) : (
                  <Facts
                    entries={detail.devices.map((device) => [
                      device.machine ?? "—",
                      <span>
                        <Tag tone={device.active ? "ok" : undefined}>
                          {device.active ? t.identity.enabled : t.identity.disabled}
                        </Tag>{" "}
                        {timestamp(device.created_at)}
                      </span>,
                    ])}
                  />
                )}
                {mayManage && (
                  <div className="row">
                    <button disabled={busy} onClick={() => setPending("revoke")}>
                      {t.members.revokeSessions}
                    </button>
                  </div>
                )}
              </Panel>

              <Panel title={t.members.identities}>
                {detail.identities.length === 0 ? (
                  <p>{t.state.empty}</p>
                ) : (
                  <Facts
                    entries={detail.identities.map((identity) => [
                      identity.provider,
                      <span>
                        <Tag tone={identity.verification === "verified" ? "ok" : "warn"}>
                          {identity.verification}
                        </Tag>{" "}
                        {timestamp(identity.last_seen)}
                      </span>,
                    ])}
                  />
                )}
              </Panel>

              {mayManage && (
                <Panel>
                  <div className="row">
                    <button
                      data-variant={suspended ? undefined : "danger"}
                      disabled={busy}
                      onClick={() => setPending(suspended ? "reactivate" : "suspend")}
                    >
                      {suspended ? t.members.reactivate : t.members.suspend}
                    </button>
                    <button
                      data-variant="danger"
                      disabled={busy}
                      onClick={() => setPending("delete")}
                    >
                      {t.members.remove}
                    </button>
                  </div>
                </Panel>
              )}
            </>
          );
        }}
      </AsyncSection>
    </>
  );
}

function AddMember({
  organizationId,
  onAdded,
}: {
  organizationId: string;
  onAdded: (userId: string) => void;
}) {
  const { control } = useSession();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<string>(MEMBER_ROLES[0]);
  const [cohort, setCohort] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function add() {
    setBusy(true);
    setFailure(null);
    try {
      const created = await control!.createMember(organizationId, {
        email: email.trim(),
        member_role: role,
        cohort: cohort.trim(),
      });
      setOpen(false);
      setEmail("");
      setCohort("");
      onAdded(created.member.user_id);
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

  if (!open) {
    return (
      <div className="row">
        <button data-variant="primary" onClick={() => setOpen(true)}>
          {t.members.addMember}
        </button>
      </div>
    );
  }

  return (
    <Panel title={t.members.addMember}>
      <p style={{ color: "var(--text-muted)" }}>{t.members.addMemberLead}</p>
      <label htmlFor="new-member-email">
        {t.members.email}
        <input
          id="new-member-email"
          type="email"
          autoComplete="off"
          value={email}
          disabled={busy}
          onChange={(event) => setEmail(event.target.value)}
        />
      </label>
      <label htmlFor="new-member-role">
        {t.members.memberType}
        <select
          id="new-member-role"
          value={role}
          disabled={busy}
          onChange={(event) => setRole(event.target.value)}
        >
          {MEMBER_ROLES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>
      <label htmlFor="new-member-cohort">
        {t.members.groups}
        <input
          id="new-member-cohort"
          type="text"
          value={cohort}
          disabled={busy}
          onChange={(event) => setCohort(event.target.value)}
        />
      </label>
      {failure && <Notice tone="danger">{failure}</Notice>}
      <div className="row">
        <button
          data-variant="primary"
          disabled={busy || email.trim() === ""}
          onClick={() => void add()}
        >
          {busy ? t.members.adding : t.members.add}
        </button>
        <button data-variant="quiet" disabled={busy} onClick={() => setOpen(false)}>
          {t.identity.cancel}
        </button>
      </div>
    </Panel>
  );
}

export function Members() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const mayAdd = can(session, CAPABILITY.identityManage);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const [state, retry] = useResource(
    control && organizationId
      ? () => control.members(organizationId, { query, status, limit: 25 })
      : null,
    [control, organizationId, query, status, reload],
  );

  if (selected) {
    return (
      <MemberDetailView
        userId={selected}
        onClose={() => {
          setSelected(null);
          setReload((n) => n + 1);
        }}
        onChanged={() => setReload((n) => n + 1)}
      />
    );
  }

  return (
    <>
      <div className="page-head">
        <h1>{t.members.title}</h1>
        <p>{t.members.lead}</p>
      </div>
      <Notice>{t.members.boundary}</Notice>
      {mayAdd && (
        <AddMember
          organizationId={organizationId}
          onAdded={(userId) => {
            setReload((n) => n + 1);
            setSelected(userId);
          }}
        />
      )}

      <Panel>
        <label htmlFor="member-query">
          {t.members.search}
          <input
            id="member-query"
            type="text"
            value={query}
            autoComplete="off"
            placeholder={t.members.searchPlaceholder}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <div className="row">
          {[
            { value: "", label: t.members.filterAll },
            { value: "active", label: t.members.filterActive },
            { value: "disabled", label: t.members.filterDisabled },
          ].map((filter) => (
            <button
              key={filter.value}
              data-variant={status === filter.value ? "primary" : undefined}
              onClick={() => setStatus(filter.value)}
            >
              {filter.label}
            </button>
          ))}
        </div>
      </Panel>

      <AsyncSection state={state} onRetry={retry}>
        {(view) =>
          view.members.length === 0 ? (
            <Panel>
              <p>{t.members.none}</p>
            </Panel>
          ) : (
            <Panel title={t.members.count(view.members.length, view.total)}>
              <Facts
                entries={view.members.map((member: MemberView) => [
                  member.email,
                  <span className="row">
                    <Tag tone={member.status === "active" ? "ok" : "warn"}>
                      {member.status}
                    </Tag>
                    <span className="mono">{member.member_type ?? "—"}</span>
                    <button onClick={() => setSelected(member.user_id)}>
                      {t.members.open}
                    </button>
                  </span>,
                ])}
              />
              {view.has_more && <p className="hint">{t.members.more}</p>}
            </Panel>
          )
        }
      </AsyncSection>
    </>
  );
}
