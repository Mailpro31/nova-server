/**
 * Éléments d'interface partagés.
 *
 * Peu nombreux et volontairement ternes : une console d'administration se lit,
 * elle ne se contemple pas.
 */

import type { ReactNode } from "react";
import { t } from "../lib/messages";

export function Panel({
  title,
  action,
  children,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      {(title || action) && (
        <header className="panel-head">
          {title && <h2>{title}</h2>}
          {action}
        </header>
      )}
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function Facts({ entries }: { entries: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {entries.map(([label, value]) => (
        <div key={label} style={{ display: "contents" }}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Tag({
  tone,
  children,
}: {
  tone?: "ok" | "warn" | "danger";
  children: ReactNode;
}) {
  return (
    <span className="tag" data-tone={tone}>
      {children}
    </span>
  );
}

export function Notice({
  tone,
  children,
}: {
  tone?: "danger";
  children: ReactNode;
}) {
  return (
    <p className="notice" data-tone={tone} role={tone === "danger" ? "alert" : undefined}>
      {children}
    </p>
  );
}

/**
 * Les quatre états d'un écran qui dépend du réseau.
 *
 * Les rassembler ici évite l'écran blanc : aucune page ne peut oublier le cas
 * « en cours » ou le cas « en échec », puisqu'elle ne les écrit pas elle-même.
 */
export function AsyncSection<T>({
  state,
  children,
  onRetry,
  empty,
}: {
  state: { status: "loading" | "error" | "ready"; data?: T; error?: string };
  children: (data: T) => ReactNode;
  onRetry?: () => void;
  empty?: ReactNode;
}) {
  if (state.status === "loading") {
    return (
      <p className="notice" aria-live="polite">
        {t.state.loading}
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <div className="stack">
        <Notice tone="danger">{state.error}</Notice>
        {onRetry && (
          <div>
            <button onClick={onRetry}>{t.state.retry}</button>
          </div>
        )}
      </div>
    );
  }
  if (state.data === undefined || state.data === null) {
    return <p className="notice">{empty ?? t.state.empty}</p>;
  }
  return <>{children(state.data)}</>;
}

export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  tone,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  tone?: "danger";
  busy?: boolean;
  /**
   * Refus renvoye par le serveur.
   *
   * Il s'affiche **ici**, dans la boite ouverte : le panneau qui la remplace
   * n'est plus rendu, et une erreur affichee la-bas resterait invisible.
   * L'utilisateur voyait alors son clic ne rien produire.
   */
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="panel" role="alertdialog" aria-modal="true" aria-label={title}>
      <div className="panel-body">
        <h2>{title}</h2>
        <p style={{ color: "var(--text-muted)" }}>{body}</p>
        {error && <Notice tone="danger">{error}</Notice>}
        <div className="row">
          <button data-variant={tone === "danger" ? "danger" : undefined}
                  onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
          <button data-variant="quiet" onClick={onCancel} disabled={busy}>
            {t.identity.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}
