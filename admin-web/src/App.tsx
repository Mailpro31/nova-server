/**
 * Routage et garde d'accès.
 *
 * Une seule porte : `<Console>` n'est monté que lorsque la session
 * d'administration existe. Les pages ne vérifient rien elles-mêmes, et il n'y a
 * donc aucun instant où un écran d'administration s'affiche avant une
 * redirection.
 */

import { useEffect } from "react";
import {
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  AccessDenied,
  OrganizationBootstrap,
  SignIn,
  StepUpPrompt,
} from "./components/AuthScreens";
import { peekHandoffOrganization } from "./lib/handoff";
import { t } from "./lib/messages";
import { secondsRemaining } from "./lib/session";
import { useSession } from "./lib/useAdminSession";
import { Administrators } from "./pages/Administrators";
import { Identity } from "./pages/Identity";
import { Members } from "./pages/Members";
import { Overview } from "./pages/Overview";
import { Configuration, Deployment, Diagnostics, Security } from "./pages/Rest";

const PAGES = [
  { path: "overview", label: t.nav.overview, element: <Overview /> },
  { path: "identity", label: t.nav.identity, element: <Identity /> },
  {
    path: "administrators",
    label: t.nav.administrators,
    element: <Administrators />,
  },
  { path: "members", label: t.nav.members, element: <Members /> },
  { path: "deployment", label: t.nav.deployment, element: <Deployment /> },
  { path: "configuration", label: t.nav.configuration, element: <Configuration /> },
  { path: "security", label: t.nav.security, element: <Security /> },
  { path: "diagnostics", label: t.nav.diagnostics, element: <Diagnostics /> },
];

function Console() {
  const { session, signOutAdmin, signOutCompletely } = useSession();
  const remaining = session.admin ? secondsRemaining(session.admin) : 0;

  return (
    <div className="shell">
      <nav className="sidebar" aria-label={t.product}>
        <div className="brand">
          {t.product}
          <small>{session.organizationName}</small>
        </div>
        <div className="nav">
          {PAGES.map((page) => (
            <NavLink key={page.path} to={page.path} end>
              {page.label}
            </NavLink>
          ))}
        </div>
      </nav>
      <div className="main">
        <header className="header">
          <strong>{session.organizationName}</strong>
          <div className="header-meta">
            <span>
              {t.session.role}: <span className="mono">{session.admin?.role}</span>
            </span>
            <span>
              {t.session.expiresIn} {Math.round(remaining / 60)} min
            </span>
            <button data-variant="quiet" onClick={() => void signOutAdmin()}>
              {t.session.signOut}
            </button>
            <button data-variant="quiet" onClick={() => void signOutCompletely()}>
              {t.session.signOutAll}
            </button>
          </div>
        </header>
        <main className="content">
          <Routes>
            <Route index element={<Navigate to="overview" replace />} />
            {PAGES.map((page) => (
              <Route key={page.path} path={page.path} element={page.element} />
            ))}
            <Route path="*" element={<Navigate to="overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

/**
 * Ouvre l'organisation désignée par l'URL, puis choisit l'écran.
 *
 * L'identifiant vient du chemin — `/o/<slug>` — et non d'une adresse saisie.
 * Aucun identifiant interne n'apparaît dans l'URL.
 */
function OrganizationRoutes() {
  const { organizationSlug } = useParams();
  const { session, openOrganization } = useSession();

  useEffect(() => {
    if (organizationSlug && session.organizationSlug !== organizationSlug) {
      // Changer de slug repart de zéro : découverte, session, portée. Rien de
      // l'organisation précédente ne subsiste.
      void openOrganization(organizationSlug);
    }
  }, [organizationSlug, session.organizationSlug, openOrganization]);

  switch (session.phase) {
    case "discovering":
      return <OrganizationBootstrap />;
    case "signed_out":
      return <SignIn />;
    case "signed_in":
    case "step_up_required":
      return <StepUpPrompt />;
    case "access_denied":
      return <AccessDenied />;
    case "administering":
      return <Console />;
  }
}

/** Retour d'autorisation : consomme le code, puis quitte l'URL porteuse. */
function AuthCallback() {
  const { completeAuthorization } = useSession();
  const navigate = useNavigate();

  useEffect(() => {
    // Le slug est lu dans le sas, pas dans la session : la redirection vient de
    // recharger la page, et la session n'est reconstruite qu'à l'intérieur de
    // `completeAuthorization`.
    const slug = peekHandoffOrganization();
    void completeAuthorization(window.location.search).then(() => {
      // Le code d'autorisation ne doit pas rester dans la barre d'adresse, ni
      // dans l'historique du navigateur.
      navigate(slug ? `/o/${slug}` : "/", { replace: true });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="centered">
      <div className="card">
        <p>{t.state.loading}</p>
      </div>
    </main>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<OrganizationBootstrap />} />
      <Route path="/callback" element={<AuthCallback />} />
      <Route path="/o/:organizationSlug/*" element={<OrganizationRoutes />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
