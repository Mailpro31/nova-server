/**
 * Écrans qui précèdent la console : découverte, connexion, réauthentification,
 * refus.
 *
 * Aucun ne montre de contenu d'administration. Le garde du `SessionProvider`
 * décide lequel afficher, et la console n'est montée qu'une fois la session
 * d'administration obtenue — il n'y a donc rien à faire clignoter avant une
 * redirection.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiClient } from "../lib/api";
import type { AuthProvidersView } from "../lib/controlPlane";
import { ControlPlane } from "../lib/controlPlane";
import { t } from "../lib/messages";
import { useSession } from "../lib/useAdminSession";
import { Notice } from "./primitives";

export function OrganizationBootstrap() {
  const { openOrganization, busy, error } = useSession();
  const navigate = useNavigate();
  const [slug, setSlug] = useState("");
  const invalid = slug.trim().length === 0;

  /**
   * Resoudre l'organisation ne suffit pas : il faut aussi y aller.
   *
   * La session passe bien en « connexion attendue », mais l'URL reste `/`, dont
   * la route reaffiche cet ecran — l'utilisateur cliquait sans que rien ne
   * bouge. La navigation appartient a cet ecran, et non a `openOrganization`,
   * que `OrganizationRoutes` appelle deja depuis l'URL : y naviguer aussi
   * produirait une boucle.
   */
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (invalid) return;
    const resolved = await openOrganization(slug.trim());
    if (resolved) navigate(`/o/${encodeURIComponent(resolved)}`);
  }

  return (
    <main className="centered">
      <form className="card" onSubmit={(event) => void submit(event)}>
        <h1>{t.bootstrap.title}</h1>
        <p>{t.bootstrap.prompt}</p>
        <label htmlFor="organization">
          {t.bootstrap.field}
          <input
            id="organization"
            name="organization"
            type="text"
            autoComplete="organization"
            autoFocus
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            aria-describedby="organization-hint"
          />
        </label>
        <p className="hint" id="organization-hint">
          {t.bootstrap.hint}
        </p>
        {error && <Notice tone="danger">{error}</Notice>}
        <button type="submit" data-variant="primary" disabled={busy || invalid}>
          {busy ? t.bootstrap.looking : t.bootstrap.submit}
        </button>
      </form>
    </main>
  );
}

const PROVIDER_LABELS: Record<string, string> = {
  microsoft_entra: t.signIn.microsoft,
  google_workspace: t.signIn.google,
  oidc: t.signIn.oidc,
};

export function SignIn() {
  const { session, signIn, busy, error, reportFailure } = useSession();
  const [providers, setProviders] = useState<AuthProvidersView | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!session.serviceEndpoint) return;
    const plane = new ControlPlane(new ApiClient({ baseUrl: session.serviceEndpoint }));
    plane
      .authProviders()
      .then(setProviders)
      .catch(reportFailure)
      .finally(() => setLoading(false));
  }, [session.serviceEndpoint, reportFailure]);

  // Le code e-mail hérité n'ouvre pas l'administration : le proposer ici
  // conduirait à un refus quelques secondes plus tard.
  const usable = (providers?.provider_configs ?? []).filter(
    (config) => config.type !== "legacy_email_code",
  );

  return (
    <main className="centered">
      <div className="card">
        <h1>
          {t.signIn.title} {session.organizationName}
        </h1>
        <p>{t.signIn.lead}</p>
        {error && <Notice tone="danger">{error}</Notice>}
        <div className="stack">
          {loading && <p className="hint">{t.state.loading}</p>}
          {!loading && usable.length === 0 && <Notice>{t.signIn.none}</Notice>}
          {usable.map((config) => (
            <button
              key={config.id}
              data-variant="primary"
              disabled={busy}
              onClick={() => void signIn(config.id, config.type)}
            >
              {config.display_name ?? PROVIDER_LABELS[config.type] ?? t.signIn.oidc}
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}

export function StepUpPrompt() {
  const { startStepUp, signOutCompletely, busy, error } = useSession();
  return (
    <main className="centered">
      <div className="card">
        <h1>{t.stepUp.title}</h1>
        <p>{t.stepUp.lead}</p>
        <p className="hint">{t.stepUp.note}</p>
        {error && <Notice tone="danger">{error}</Notice>}
        <button data-variant="primary" disabled={busy} onClick={() => void startStepUp()}>
          {busy ? t.stepUp.working : t.stepUp.action}
        </button>
        <button data-variant="quiet" onClick={() => void signOutCompletely()}>
          {t.session.signOutAll}
        </button>
      </div>
    </main>
  );
}

export function AccessDenied() {
  const { signOutCompletely } = useSession();
  return (
    <main className="centered">
      <div className="card">
        <h1>{t.denied.title}</h1>
        <p>{t.denied.lead}</p>
        <p className="hint">{t.denied.hint}</p>
        <button onClick={() => void signOutCompletely()}>{t.session.signOutAll}</button>
      </div>
    </main>
  );
}
