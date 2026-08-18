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
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import type { ProviderConfigView } from "../lib/controlPlane";
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
  const [replacing, setReplacing] = useState(false);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setFailure(null);
    try {
      await action();
      setConfirming(false);
      setReplacing(false);
      setSecret("");
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
        mayManage && config.enabled ? (
          <button data-variant="danger" onClick={() => setConfirming(true)}>
            {t.identity.disable}
          </button>
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

      {mayManage && config.provider_type === "oidc" && (
        <div className="stack">
          {replacing ? (
            <>
              <label htmlFor={`secret-${config.id}`}>
                {t.identity.newSecret}
                <input
                  id={`secret-${config.id}`}
                  type="password"
                  value={secret}
                  autoComplete="off"
                  onChange={(event) => setSecret(event.target.value)}
                />
              </label>
              <div className="row">
                <button
                  data-variant="primary"
                  disabled={busy || secret.length === 0}
                  onClick={() =>
                    void run(() =>
                      // Seul le nouveau secret est transmis : l'ancien n'a
                      // jamais quitté le serveur, et n'a pas à revenir.
                      control!.updateProviderConfig(config.id, {
                        provider_type: config.provider_type,
                        display_name: config.display_name,
                        enabled: config.enabled,
                        oidc_issuer: config.oidc_issuer,
                        oidc_client_id: config.oidc_client_id,
                        oidc_client_auth_method: config.oidc_client_auth_method,
                        oidc_client_secret: secret,
                      }),
                    )
                  }
                >
                  {t.identity.save}
                </button>
                <button data-variant="quiet" onClick={() => setReplacing(false)}>
                  {t.identity.cancel}
                </button>
              </div>
            </>
          ) : (
            <div>
              <button onClick={() => setReplacing(true)}>
                {t.identity.replaceSecret}
              </button>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

export function Identity() {
  const { control } = useSession();
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
