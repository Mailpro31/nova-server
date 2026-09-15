/**
 * Gestion des administrateurs de l'organisation.
 *
 * Toutes les mutations désignent un `user_id`. L'adresse n'est affichée que pour
 * qu'on sache de qui il s'agit — c'est un attribut, et depuis la Phase 23 il
 * peut changer.
 *
 * Ce que cet écran ne fait pas : créer une personne. On attribue un rôle à un
 * compte qui existe déjà ; inviter ou provisionner relève d'autre chose.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { AdministratorView, MemberView } from "../lib/controlPlane";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, ConfirmDialog, Notice, Panel, Tag } from "../components/primitives";

const ROLES = [
  {
    value: "organization_admin",
    label: t.administrators.roleOrganizationAdmin,
    hint: t.administrators.roleOrganizationAdminHint,
  },
  {
    value: "it_admin",
    label: t.administrators.roleItAdmin,
    hint: t.administrators.roleItAdminHint,
  },
  {
    value: "read_only",
    label: t.administrators.roleReadOnly,
    hint: t.administrators.roleReadOnlyHint,
  },
  {
    value: "content_editor",
    label: t.administrators.roleContentEditor,
    hint: t.administrators.roleContentEditorHint,
  },
] as const;

function roleLabel(role: string): string {
  return ROLES.find((entry) => entry.value === role)?.label ?? t.administrators.roleMember;
}

/** Recherche d'un compte à promouvoir. Rien ne s'affiche sans terme saisi. */
function GrantAccess({ onGranted }: { onGranted: () => void }) {
  const { control, session } = useSession();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemberView[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [granted, setGranted] = useState<string | null>(null);

  const organizationId = session.admin?.organizationId ?? "";

  async function search(term: string) {
    setQuery(term);
    setGranted(null);
    if (term.trim().length < 2) {
      setResults(null);
      return;
    }
    try {
      // La recherche et la liste des membres ont fusionné en Phase 27 : une
      // seule route, paginée, qui se restreint quand on lui donne un terme.
      const found = await control!.members(organizationId, {
        query: term.trim(),
        limit: 25,
      });
      setResults(found.members);
      setFailure(null);
    } catch (raised) {
      setFailure(
        raised instanceof ApiError
          ? errorMessage(raised.kind, raised.code)
          : errorMessage("server"),
      );
    }
  }

  async function grant(member: MemberView, role: string) {
    setBusy(true);
    setFailure(null);
    try {
      await control!.setAdministratorRole(organizationId, member.user_id, role);
      setGranted(member.email);
      setQuery("");
      setResults(null);
      onGranted();
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
    <Panel title={t.administrators.addTitle}>
      <p style={{ color: "var(--text-muted)" }}>{t.administrators.addLead}</p>
      <label htmlFor="member-search">
        {t.administrators.searchLabel}
        <input
          id="member-search"
          type="text"
          value={query}
          autoComplete="off"
          placeholder={t.administrators.searchPlaceholder}
          onChange={(event) => void search(event.target.value)}
        />
      </label>
      {query.trim().length > 0 && query.trim().length < 2 && (
        <p className="hint">{t.administrators.searchShort}</p>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}
      {granted && <Notice>{t.administrators.grantedTo(granted)}</Notice>}
      {results?.length === 0 && <Notice>{t.administrators.searchEmpty}</Notice>}
      {results && results.length > 0 && (
        <div className="stack">
          {results.map((member) => (
            <div className="row spread" key={member.user_id}>
              <span>
                {member.email}{" "}
                <Tag>{roleLabel(member.security_role)}</Tag>
              </span>
              <div className="row">
                {ROLES.map((role) => (
                  <button
                    key={role.value}
                    disabled={busy || member.security_role === role.value}
                    onClick={() => void grant(member, role.value)}
                  >
                    {role.label}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

function AdministratorRow({
  administrator,
  soleManager,
  onChanged,
}: {
  administrator: AdministratorView;
  soleManager: string | null;
  onChanged: () => void;
}) {
  const { control, session } = useSession();
  const mayManage = can(session, CAPABILITY.identityManage);
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  // Le serveur refuserait de toute façon : désactiver l'action évite de la
  // proposer pour rien. Ce n'est pas la protection — elle est côté serveur.
  const isSoleManager = soleManager === administrator.user_id;

  async function apply(role: string) {
    setBusy(true);
    setFailure(null);
    try {
      await control!.setAdministratorRole(
        session.admin?.organizationId ?? "",
        administrator.user_id,
        role,
      );
      setPending(null);
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
    const removing = pending === "member";
    return (
      <ConfirmDialog
        title={removing ? t.administrators.removeTitle : t.administrators.changeTitle}
        body={removing ? t.administrators.removeBody : t.administrators.changeBody}
        confirmLabel={
          removing ? t.administrators.removeConfirm : t.administrators.changeConfirm
        }
        tone={removing ? "danger" : undefined}
        busy={busy}
        error={failure}
        onConfirm={() => void apply(pending)}
        onCancel={() => {
          setPending(null);
          setFailure(null);
        }}
      />
    );
  }

  return (
    <Panel title={administrator.email}>
      <div className="row spread">
        <div className="row">
          <Tag tone={administrator.status === "active" ? "ok" : "warn"}>
            {administrator.status}
          </Tag>
          <Tag>{roleLabel(administrator.security_role)}</Tag>
        </div>
        {mayManage && (
          <div className="row">
            {ROLES.filter((role) => role.value !== administrator.security_role).map(
              (role) => (
                <button
                  key={role.value}
                  disabled={busy}
                  title={role.hint}
                  onClick={() => setPending(role.value)}
                >
                  {role.label}
                </button>
              ),
            )}
            <button
              data-variant="danger"
              disabled={busy || isSoleManager}
              title={isSoleManager ? t.administrators.lastManager : undefined}
              onClick={() => setPending("member")}
            >
              {t.administrators.removeAccess}
            </button>
          </div>
        )}
      </div>
      {failure && <Notice tone="danger">{failure}</Notice>}
      {isSoleManager && <Notice>{t.administrators.lastManager}</Notice>}
    </Panel>
  );
}

export function Administrators() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const [state, retry] = useResource(
    control && organizationId ? () => control.administrators(organizationId) : null,
    [control, organizationId],
  );
  const mayManage = can(session, CAPABILITY.identityManage);

  return (
    <>
      <div className="page-head">
        <h1>{t.administrators.title}</h1>
        <p>{t.administrators.lead}</p>
      </div>
      <Notice>{t.administrators.bootstrapNote}</Notice>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => (
          <>
            {view.administrators.length === 0 ? (
              <Panel>
                <p>{t.administrators.none}</p>
              </Panel>
            ) : (
              view.administrators.map((administrator) => (
                <AdministratorRow
                  key={administrator.user_id}
                  administrator={administrator}
                  soleManager={view.sole_identity_manager}
                  onChanged={retry}
                />
              ))
            )}
            {mayManage && <GrantAccess onGranted={retry} />}
          </>
        )}
      </AsyncSection>
    </>
  );
}
