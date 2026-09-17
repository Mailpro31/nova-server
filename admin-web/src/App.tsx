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
import { NAV_SECTIONS } from "./lib/navigation";
import { secondsRemaining } from "./lib/session";
import { useSession } from "./lib/useAdminSession";
import { Administrators } from "./pages/Administrators";
import { Groups } from "./pages/Groups";
import { Identity } from "./pages/Identity";
import { Learn } from "./pages/Learn";
import { Limits } from "./pages/Limits";
import { Members } from "./pages/Members";
import { Packages } from "./pages/Packages";
import { Provisioning } from "./pages/Provisioning";
import { Overview } from "./pages/Overview";
import { AuditLog, Configuration, Deployment, Diagnostics, Security } from "./pages/Rest";

const PAGES = [
  { path: "overview", label: t.nav.overview, element: <Overview /> },
  { path: "identity", label: t.nav.identity, element: <Identity /> },
  { path: "provisioning", label: t.nav.provisioning, element: <Provisioning /> },
  {
    path: "administrators",
    label: t.nav.administrators,
    element: <Administrators />,
  },
  { path: "members", label: t.nav.members, element: <Members /> },
  { path: "groups", label: t.nav.groups, element: <Groups /> },
  { path: "learn", label: t.nav.learn, element: <Learn /> },
  { path: "packages", label: t.nav.packages, element: <Packages /> },
  { path: "limits", label: t.nav.limits, element: <Limits /> },
  { path: "deployment", label: t.nav.deployment, element: <Deployment /> },
  { path: "configuration", label: t.nav.configuration, element: <Configuration /> },
  { path: "security", label: t.nav.security, element: <Security /> },
  { path: "audit", label: t.nav.audit, element: <AuditLog /> },
  { path: "diagnostics", label: t.nav.diagnostics, element: <Diagnostics /> },
];

const PAGE_BY_PATH = new Map(PAGES.map((page) => [page.path, page]));

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
          {NAV_SECTIONS.map((section) => {
            const pages = section.pages.flatMap((path) => {
              const page = PAGE_BY_PATH.get(path);
              return page ? [page] : [];
            });
            // Un domaine d'une seule page n'a pas de titre : son lien porte
            // le nom du domaine. Au-delà, le titre regroupe ses pages.
            const single = pages.length === 1;
            return (
              <div
                key={section.id}
                className="nav-section"
                role="group"
                aria-label={t.navGroups[section.id]}
              >
                {!single && (
                  <span className="nav-section-title">{t.navGroups[section.id]}</span>
                )}
                {pages.map((page) => (
                  <NavLink key={page.path} to={page.path} end>
                    {single ? t.navGroups[section.id] : page.label}
                  </NavLink>
                ))}
              </div>
            );
          })}
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
