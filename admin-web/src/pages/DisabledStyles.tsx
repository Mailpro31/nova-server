/**
 * Styles désactivés, un par un.
 *
 * Chaque interrupteur écrit la policy entière, sur la révision affichée : le
 * PUT remplace le document, et le serveur refuse une révision dépassée. Après
 * chaque changement, l'écran relit le serveur plutôt que de supposer.
 */

import { useState } from "react";
import { ApiError } from "../lib/api";
import { errorMessage, t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import {
  disabledStyleIds,
  stylesCatalog,
  withStyleDisabled,
} from "../lib/styles";
import { useResource } from "../lib/useResource";
import { useSession } from "../lib/useAdminSession";
import { AsyncSection, Notice, Panel } from "../components/primitives";

export function DisabledStyles() {
  const { control, session } = useSession();
  const organizationId = session.admin?.organizationId ?? "";
  const [state, retry] = useResource(control ? () => control.policy() : null, [
    control,
  ]);
  const [packages] = useResource(
    control && organizationId ? () => control.packages(organizationId) : null,
    [control, organizationId],
  );
  const mayManage = can(session, CAPABILITY.policyManage);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  function reload() {
    setFailure(null);
    setConflict(false);
    retry();
  }

  return (
    <Panel title={t.styles.title}>
      <p style={{ color: "var(--text-muted)" }}>{t.styles.lead}</p>
      <AsyncSection state={state} onRetry={retry}>
        {(view) => {
          const disabled = new Set(disabledStyleIds(view.settings));
          const catalog = stylesCatalog(packages.data?.packages ?? []);

          async function toggle(styleId: string, disable: boolean) {
            setBusy(styleId);
            setFailure(null);
            setConflict(false);
            try {
              const next = withStyleDisabled(view.settings, styleId, disable);
              await control!.updatePolicy(view.revision, next);
              retry();
            } catch (raised) {
              if (raised instanceof ApiError && raised.kind === "conflict") {
                setConflict(true);
              } else {
                setFailure(
                  raised instanceof ApiError
                    ? errorMessage(raised.kind, raised.code)
                    : errorMessage("server"),
                );
              }
            } finally {
              setBusy(null);
            }
          }

          return (
            <>
              {catalog.map((style) => (
                <div className="policy-setting" key={style.id}>
                  <div>
                    <strong>{style.name}</strong>
                    <p className="policy-hint" style={{ margin: 0 }}>
                      {style.origin === "builtin"
                        ? t.styles.builtin
                        : t.styles.organization}
                    </p>
                  </div>
                  <label className="row">
                    <input
                      type="checkbox"
                      aria-label={style.name}
                      checked={!disabled.has(style.id)}
                      disabled={!mayManage || busy !== null}
                      onChange={(event) =>
                        void toggle(style.id, !event.target.checked)
                      }
                    />
                    <span>
                      {disabled.has(style.id)
                        ? t.styles.disabled
                        : t.styles.enabled}
                    </span>
                  </label>
                </div>
              ))}
              {!mayManage && <p className="policy-hint">{t.styles.readOnly}</p>}
              {conflict && (
                <Notice tone="danger">
                  {t.styles.conflict}{" "}
                  <button onClick={reload}>{t.styles.reload}</button>
                </Notice>
              )}
              {failure && <Notice tone="danger">{failure}</Notice>}
            </>
          );
        }}
      </AsyncSection>
    </Panel>
  );
}
