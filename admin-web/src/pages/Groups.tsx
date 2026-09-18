/**
 * Groupes de l'organisation.
 *
 * Un groupe sert à viser une leçon ou à changer des réglages pour une partie de
 * l'organisation. Ceux créés ici se renomment, se suppriment et gèrent leurs
 * membres. Ceux venus de l'annuaire se montrent sans aucun bouton de
 * modification : l'annuaire en reste le seul auteur, et deux auteurs finiraient
 * par se contredire.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { GroupView } from "../lib/controlPlane";
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

function failureText(raised: unknown): string {
  return raised instanceof ApiError
    ? errorMessage(raised.kind, raised.code)
    : errorMessage("server");
}

function GroupMembers({
  organizationId,
  group,
  editable,
  onChanged,
}: {
  organizationId: string;
  group: GroupView;
  editable: boolean;
  onChanged: () => void;
}) {
  const { control } = useSession();
  const [members, retryMembers] = useResource(
    control ? () => control.groupMembers(organizationId, group.id) : null,
    [control, organizationId, group.id, group.member_count],
  );
  const [search, setSearch] = useState("");
  const [matches] = useResource(
    control && editable && search.trim().length >= 2
      ? () =>
          control.members(organizationId, { query: search.trim(), limit: 8 })
      : null,
    [control, editable, organizationId, search],
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      retryMembers();
      onChanged();
    } catch (raised) {
      setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  const memberIds = new Set(
    (members.data?.members ?? []).map((member) => member.user_id),
  );

  return (
    <div className="stack">
      <h3 className="policy-group">{t.groups.members}</h3>
      <AsyncSection state={members} onRetry={retryMembers}>
        {(view) =>
          view.members.length === 0 ? (
            <p className="policy-hint">{t.groups.noMembers}</p>
          ) : (
            <>
              {view.members.map((member) => (
                <div className="policy-setting" key={member.user_id}>
                  <span>{member.email}</span>
                  {editable && (
                    <button
                      data-variant="quiet"
                      disabled={busy}
                      onClick={() =>
                        void run(() =>
                          control!.removeGroupMember(
                            organizationId,
                            group.id,
                            member.user_id,
                          ),
                        )
                      }
                    >
                      {t.groups.remove}
                    </button>
                  )}
                </div>
              ))}
            </>
          )
        }
      </AsyncSection>

      {editable && (
        <>
          <label className="field">
            <span>{t.groups.addMember}</span>
            <input
              type="text"
              autoComplete="off"
              value={search}
              placeholder={t.groups.searchPlaceholder}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          {search.trim().length >= 2 && (
            <AsyncSection state={matches} onRetry={() => setSearch(search)}>
              {(view) => {
                const candidates = view.members.filter(
                  (member) => !memberIds.has(member.user_id),
                );
                return candidates.length === 0 ? (
                  <p className="policy-hint">{t.groups.noMatch}</p>
                ) : (
                  <>
                    {candidates.map((member) => (
                      <div className="policy-setting" key={member.user_id}>
                        <span>{member.email}</span>
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(() =>
                              control!.addGroupMember(
                                organizationId,
                                group.id,
                                member.user_id,
                              ),
                            )
                          }
                        >
                          {t.groups.add}
                        </button>
                      </div>
                    ))}
                  </>
                );
              }}
            </AsyncSection>
          )}
        </>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}
    </div>
  );
}

function GroupCard({
  organizationId,
  group,
  mayManage,
  onChanged,
}: {
  organizationId: string;
  group: GroupView;
  mayManage: boolean;
  onChanged: () => void;
}) {
  const { control } = useSession();
  const editable = mayManage && group.source === "console";
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      setRenaming(null);
      setConfirmDelete(false);
      onChanged();
    } catch (raised) {
      setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  if (confirmDelete) {
    return (
      <ConfirmDialog
        title={t.groups.deleteTitle}
        body={t.groups.deleteBody}
        confirmLabel={t.groups.delete}
        tone="danger"
        busy={busy}
        error={failure}
        onConfirm={() =>
          void run(() => control!.deleteGroup(organizationId, group.id))
        }
        onCancel={() => {
          setConfirmDelete(false);
          setFailure(null);
        }}
      />
    );
  }

  return (
    <Panel
      title={group.label}
      action={
        <button data-variant="quiet" onClick={() => setOpen(!open)}>
          {open ? t.groups.close : t.groups.open}
        </button>
      }
    >
      <Facts
        entries={[
          [
            t.groups.source,
            group.source === "console" ? (
              <Tag tone="ok">{t.groups.sourceConsole}</Tag>
            ) : (
              <Tag>{t.groups.sourceDirectory}</Tag>
            ),
          ],
          [t.groups.members, t.groups.memberCount(group.member_count)],
          [
            t.groups.display,
            group.visible_to_members ? (
              <Tag tone="ok">{t.groups.visibleToMembers}</Tag>
            ) : (
              <Tag tone="warn">{t.groups.hiddenFromMembers}</Tag>
            ),
          ],
        ]}
      />
      {group.source !== "console" && (
        <p className="policy-hint">{t.groups.managedByDirectory}</p>
      )}
      {/* Un choix d'affichage de Nova : permis aussi pour un groupe de
          l'annuaire, qui reste l'auteur du groupe lui-même. */}
      {mayManage && (
        <div className="row">
          <button
            disabled={busy}
            onClick={() =>
              void run(() =>
                control!.setGroupVisibility(
                  organizationId,
                  group.id,
                  !group.visible_to_members,
                ),
              )
            }
          >
            {group.visible_to_members
              ? t.groups.hideFromMembers
              : t.groups.showToMembers}
          </button>
          <span className="policy-hint">{t.groups.visibilityHint}</span>
        </div>
      )}

      {editable &&
        (renaming !== null ? (
          <div className="row">
            <input
              aria-label={t.groups.name}
              value={renaming}
              disabled={busy}
              onChange={(event) => setRenaming(event.target.value)}
            />
            <button
              data-variant="primary"
              disabled={busy || renaming.trim() === ""}
              onClick={() =>
                void run(() =>
                  control!.renameGroup(
                    organizationId,
                    group.id,
                    renaming.trim(),
                  ),
                )
              }
            >
              {t.groups.save}
            </button>
            <button
              data-variant="quiet"
              disabled={busy}
              onClick={() => setRenaming(null)}
            >
              {t.groups.cancel}
            </button>
          </div>
        ) : (
          <div className="row">
            <button disabled={busy} onClick={() => setRenaming(group.label)}>
              {t.groups.rename}
            </button>
            <button
              data-variant="danger"
              disabled={busy}
              onClick={() => setConfirmDelete(true)}
            >
              {t.groups.delete}
            </button>
          </div>
        ))}
      {failure && <Notice tone="danger">{failure}</Notice>}

      {open && (
        <GroupMembers
          organizationId={organizationId}
          group={group}
          editable={editable}
          onChanged={onChanged}
        />
      )}
    </Panel>
  );
}

export function Groups() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const mayManage = can(session, CAPABILITY.identityManage);
  const [state, retry] = useResource(
    control && organizationId ? () => control.groups(organizationId) : null,
    [control, organizationId],
  );
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setFailure(null);
    try {
      await control!.createGroup(organizationId, name.trim());
      setName("");
      retry();
    } catch (raised) {
      setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>{t.groups.title}</h1>
        <p>{t.groups.lead}</p>
      </div>

      {mayManage ? (
        <Panel title={t.groups.create}>
          <div className="row">
            <input
              aria-label={t.groups.name}
              placeholder={t.groups.name}
              value={name}
              disabled={busy}
              onChange={(event) => setName(event.target.value)}
            />
            <button
              data-variant="primary"
              disabled={busy || name.trim() === ""}
              onClick={() => void create()}
            >
              {busy ? t.groups.creating : t.groups.create}
            </button>
          </div>
          {failure && <Notice tone="danger">{failure}</Notice>}
        </Panel>
      ) : (
        <p className="policy-hint">{t.groups.readOnly}</p>
      )}

      <AsyncSection state={state} onRetry={retry}>
        {(view) =>
          view.groups.length === 0 ? (
            <Panel>
              <p>{t.groups.empty}</p>
            </Panel>
          ) : (
            <>
              {view.groups.map((group) => (
                <GroupCard
                  key={group.id}
                  organizationId={organizationId}
                  group={group}
                  mayManage={mayManage}
                  onChanged={retry}
                />
              ))}
            </>
          )
        }
      </AsyncSection>
    </>
  );
}
