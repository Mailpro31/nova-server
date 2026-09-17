/**
 * Fournisseurs d'identité de l'organisation.
 *
 * ## Les secrets
 *
 * Un secret client n'est jamais lu, jamais affiché, jamais rempli dans un champ.
 * Le serveur ne le renvoie pas — il dit seulement s'il en existe un. La console
 * ne propose donc que de le **remplacer** : champ vide, aucun bouton « afficher ».
 * Des astérisques masquant une vraie valeur seraient pires que rien : ils
 * suggèrent que la valeur a voyagé.
 *
 * ## Chaque geste dans les deux sens
 *
 * Ajouter, modifier, désactiver et réactiver. Aucune suppression : un
 * fournisseur désactivé garde le rattachement des comptes qui l'ont utilisé.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { ProviderConfigView } from "../lib/controlPlane";
import {
  OIDC_AUTH_METHODS,
  PROVIDER_TYPES,
  newProviderProblems,
  providerPayload,
  type ProviderDraft,
} from "../lib/identity";
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

function ProviderFacts({ config }: { config: ProviderConfigView }) {
  const entries: [string, React.ReactNode][] = [
    [t.identity.type, <span className="mono">{config.provider_type}</span>],
    [
      t.identity.status,
      config.enabled ? (
        <Tag tone="ok">{t.identity.enabled}</Tag>
      ) : (
        <Tag>{t.identity.disabled}</Tag>
      ),
    ],
  ];
  if (config.entra_tenant_id) {
    entries.push(["Tenant", <span className="mono">{config.entra_tenant_id}</span>]);
  }
  if (config.google_hosted_domain) {
    entries.push(["Hosted domain", config.google_hosted_domain]);
  }
  if (config.oidc_issuer) {
    entries.push(["Issuer", <span className="mono">{config.oidc_issuer}</span>]);
    entries.push(["Client ID", <span className="mono">{config.oidc_client_id}</span>]);
    entries.push(["Token auth", config.oidc_client_auth_method ?? "none"]);
    if (config.oidc_required_claim) {
      entries.push([
        "Required claim",
        <span className="mono">
          {config.oidc_required_claim}={config.oidc_required_claim_value}
        </span>,
      ]);
    }
    entries.push([
      t.identity.secret,
      config.has_secret ? (
        <Tag tone="ok">{t.identity.secretConfigured}</Tag>
      ) : (
        <Tag>{t.identity.secretAbsent}</Tag>
      ),
    ]);
  }
  return <Facts entries={entries} />;
}

/** Les champs d'un fournisseur, selon son type. Le secret n'est jamais prérempli. */
function ProviderFields({
  draft,
  onChange,
  chooseType,
  disabled,
}: {
  draft: ProviderDraft;
  onChange: (next: ProviderDraft) => void;
  chooseType: boolean;
  disabled: boolean;
}) {
  const set = (field: keyof ProviderDraft, value: string) =>
    onChange({ ...draft, [field]: value });
  return (
    <div className="stack">
      {chooseType && (
        <label className="field">
          <span>{t.identity.providerType}</span>
          <select
            value={draft.provider_type}
            disabled={disabled}
            onChange={(event) => onChange({ provider_type: event.target.value })}
          >
            {PROVIDER_TYPES.map((type) => (
              <option key={type} value={type}>
                {t.identity.typeLabel(type)}
              </option>
            ))}
          </select>
        </label>
      )}
      <label className="field">
        <span>{t.identity.displayName}</span>
        <input
          value={draft.display_name ?? ""}
          disabled={disabled}
          onChange={(event) => set("display_name", event.target.value)}
        />
      </label>
      {draft.provider_type === "microsoft_entra" && (
        <label className="field">
          <span>{t.identity.tenantId}</span>
          <input
            className="mono"
            value={draft.entra_tenant_id ?? ""}
            disabled={disabled}
            onChange={(event) => set("entra_tenant_id", event.target.value)}
          />
        </label>
      )}
      {draft.provider_type === "google_workspace" && (
        <label className="field">
          <span>{t.identity.hostedDomain}</span>
          <input
            value={draft.google_hosted_domain ?? ""}
            disabled={disabled}
            onChange={(event) => set("google_hosted_domain", event.target.value)}
          />
        </label>
      )}
      {draft.provider_type === "oidc" && (
        <>
          <label className="field">
            <span>{t.identity.issuer}</span>
            <input
              className="mono"
              value={draft.oidc_issuer ?? ""}
              disabled={disabled}
              onChange={(event) => set("oidc_issuer", event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t.identity.clientId}</span>
            <input
              className="mono"
              value={draft.oidc_client_id ?? ""}
              disabled={disabled}
              onChange={(event) => set("oidc_client_id", event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t.identity.authMethod}</span>
            <select
              value={draft.oidc_client_auth_method ?? "client_secret_post"}
              disabled={disabled}
              onChange={(event) => set("oidc_client_auth_method", event.target.value)}
            >
              {OIDC_AUTH_METHODS.map((method) => (
                <option key={method} value={method}>
                  {t.identity.authMethodLabel(method)}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t.identity.clientSecret}</span>
            <input
              type="password"
              autoComplete="off"
              value={draft.oidc_client_secret ?? ""}
              disabled={disabled}
              onChange={(event) => set("oidc_client_secret", event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t.identity.requiredClaim}</span>
            <input
              className="mono"
              value={draft.oidc_required_claim ?? ""}
              disabled={disabled}
              onChange={(event) => set("oidc_required_claim", event.target.value)}
            />
          </label>
          <label className="field">
            <span>{t.identity.requiredClaimValue}</span>
            <input
              className="mono"
              value={draft.oidc_required_claim_value ?? ""}
              disabled={disabled}
              onChange={(event) => set("oidc_required_claim_value", event.target.value)}
            />
          </label>
        </>
      )}
    </div>
  );
}

function ProviderCard({
  config,
  onChanged,
}: {
  config: ProviderConfigView;
  onChanged: () => void;
}) {
  const { control, session } = useSession();
  const mayManage = can(session, CAPABILITY.providerManage);
  const [confirming, setConfirming] = useState(false);
  const [editing, setEditing] = useState<ProviderDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      setConfirming(false);
      setEditing(null);
      onChanged();
    } catch (raised) {
      setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  if (confirming) {
    return (
      <ConfirmDialog
        title={t.identity.disableTitle}
        body={t.identity.disableBody}
        confirmLabel={t.identity.disableConfirm}
        tone="danger"
        busy={busy}
        error={failure}
        onConfirm={() => void run(() => control!.disableProviderConfig(config.id))}
        onCancel={() => {
          setConfirming(false);
          setFailure(null);
        }}
      />
    );
  }

  return (
    <Panel
      title={config.display_name ?? config.provider_type}
      action={
        mayManage ? (
          config.enabled ? (
            <button data-variant="danger" disabled={busy} onClick={() => setConfirming(true)}>
              {t.identity.disable}
            </button>
          ) : (
            <button
              data-variant="primary"
              disabled={busy}
              onClick={() =>
                void run(() =>
                  control!.updateProviderConfig(
                    config.id,
                    providerPayload(config, { enabled: true }),
                  ),
                )
              }
            >
              {t.identity.enable}
            </button>
          )
        ) : undefined
      }
    >
      <ProviderFacts config={config} />
      {config.validation_error && (
        <Notice tone="danger">
          {t.identity.broken} — <span className="mono">{config.validation_error}</span>
        </Notice>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}

      {mayManage &&
        (editing ? (
          <div className="stack">
            <ProviderFields
              draft={editing}
              onChange={setEditing}
              chooseType={false}
              disabled={busy}
            />
            {config.provider_type === "oidc" && (
              <p className="policy-hint">{t.identity.secretKeptHint}</p>
            )}
            <div className="row">
              <button
                data-variant="primary"
                disabled={busy}
                onClick={() =>
                  void run(() =>
                    control!.updateProviderConfig(config.id, providerPayload(config, editing)),
                  )
                }
              >
                {t.identity.save}
              </button>
              <button data-variant="quiet" disabled={busy} onClick={() => setEditing(null)}>
                {t.identity.cancel}
              </button>
            </div>
          </div>
        ) : (
          <div className="row">
            <button
              onClick={() =>
                setEditing({
                  provider_type: config.provider_type,
                  display_name: config.display_name,
                  entra_tenant_id: config.entra_tenant_id,
                  google_hosted_domain: config.google_hosted_domain,
                  oidc_issuer: config.oidc_issuer,
                  oidc_client_id: config.oidc_client_id,
                  oidc_client_auth_method: config.oidc_client_auth_method,
                  oidc_required_claim: config.oidc_required_claim,
                  oidc_required_claim_value: config.oidc_required_claim_value,
                })
              }
            >
              {t.identity.edit}
            </button>
          </div>
        ))}
    </Panel>
  );
}

function AddProvider({ onAdded }: { onAdded: () => void }) {
  const { control } = useSession();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<ProviderDraft>({ provider_type: "microsoft_entra" });
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const problems = newProviderProblems(draft);

  async function create() {
    if (problems.length > 0) return;
    setBusy(true);
    setFailure(null);
    try {
      await control!.createProviderConfig({ ...draft, enabled: true });
      setOpen(false);
      setDraft({ provider_type: "microsoft_entra" });
      onAdded();
    } catch (raised) {
      setFailure(failureText(raised));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="row">
        <button data-variant="primary" onClick={() => setOpen(true)}>
          {t.identity.addProvider}
        </button>
      </div>
    );
  }

  return (
    <Panel title={t.identity.addProvider}>
      <p style={{ color: "var(--text-muted)" }}>{t.identity.addProviderLead}</p>
      <ProviderFields draft={draft} onChange={setDraft} chooseType disabled={busy} />
      {problems.length > 0 && (
        <div className="notice">
          <strong>{t.identity.problemsTitle}</strong>
          <ul className="problem-list">
            {problems.map((field) => (
              <li key={field}>{t.identity.problemLabel(field)}</li>
            ))}
          </ul>
        </div>
      )}
      {failure && <Notice tone="danger">{failure}</Notice>}
      <div className="row">
        <button
          data-variant="primary"
          disabled={busy || problems.length > 0}
          onClick={() => void create()}
        >
          {busy ? t.identity.creating : t.identity.create}
        </button>
        <button data-variant="quiet" disabled={busy} onClick={() => setOpen(false)}>
          {t.identity.cancel}
        </button>
      </div>
    </Panel>
  );
}

export function Identity() {
  const { control, session } = useSession();
  const mayManage = can(session, CAPABILITY.providerManage);
  const [state, retry] = useResource(
    control ? () => control.providerConfigs() : null,
    [control],
  );

  return (
    <>
      <div className="page-head">
        <h1>{t.identity.title}</h1>
        <p>{t.identity.lead}</p>
      </div>
      {mayManage && <AddProvider onAdded={retry} />}
      <AsyncSection state={state} onRetry={retry}>
        {(view) =>
          view.configs.length === 0 ? (
            <Panel>
              <p>{t.state.notConfigured}</p>
            </Panel>
          ) : (
            <>
              {view.configs.map((config) => (
                <ProviderCard key={config.id} config={config} onChanged={retry} />
              ))}
            </>
          )
        }
      </AsyncSection>
    </>
  );
}
