/**
 * Machine à états de la session, et garde d'accès unique.
 *
 * Toute la console passe par ici. Une vérification dispersée — `if (token)` dans
 * chaque page — finirait par manquer un écran, et ce serait justement l'écran
 * sensible.
 *
 * Le serveur reste l'autorité : ce garde évite des appels voués à l'échec et
 * empêche d'afficher un écran d'administration à quelqu'un qui n'y a pas droit.
 * Il ne protège rien à lui seul, et ne prétend pas le faire.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiClient, ApiError, discoverOrganization } from "./api";
import { errorMessage } from "./messages";
import { ControlPlane } from "./controlPlane";
import { clearHandoff, consumeHandoff, rememberHandoff } from "./handoff";
import { createPkce, readAuthorizationResponse, redirectUri } from "./pkce";
import {
  initialSession,
  sessionAfterFailure,
  type SessionState,
} from "./session";

/** Origine du plan de contrôle : injectée au build, jamais saisie par l'utilisateur. */
const CONTROL_URL: string =
  (import.meta.env?.VITE_NOVA_API_URL as string | undefined) ?? window.location.origin;

interface SessionContextValue {
  session: SessionState;
  control: ControlPlane | null;
  error: string | null;
  busy: boolean;
  /** Renvoie le slug resolu, ou `null` si la decouverte a echoue. */
  openOrganization: (slug: string) => Promise<string | null>;
  signIn: (providerConfigId: string | null, providerType: string) => Promise<void>;
  startStepUp: () => Promise<void>;
  completeAuthorization: (search: string) => Promise<void>;
  signOutAdmin: () => Promise<void>;
  signOutCompletely: () => Promise<void>;
  reportFailure: (error: unknown) => string;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionState>(initialSession);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Le client relit le jeton à chaque appel : il change au fil du parcours.
  const control = useMemo(() => {
    if (!session.serviceEndpoint) return null;
    return new ControlPlane(
      new ApiClient({
        baseUrl: session.serviceEndpoint,
        getToken: () => session.adminToken,
      }),
    );
  }, [session.serviceEndpoint, session.adminToken]);

  const reportFailure = useCallback((raised: unknown): string => {
    if (raised instanceof ApiError) {
      setSession((current) => sessionAfterFailure(current, raised.kind));
      const message = errorMessage(raised.kind, raised.code);
      setError(message);
      return message;
    }
    const message = errorMessage("server");
    setError(message);
    return message;
  }, []);

  const openOrganization = useCallback(async (slug: string): Promise<string | null> => {
    setBusy(true);
    setError(null);
    try {
      const found = await discoverOrganization(CONTROL_URL, slug);
      setSession({
        ...initialSession,
        phase: "signed_out",
        organizationSlug: found.slug,
        organizationName: found.displayName,
        serviceEndpoint: found.serviceEndpoint,
      });
      // Le slug canonique vient du serveur, pas de la saisie : « IPSA » et
      // « ipsa » designent la meme organisation, et c'est la forme normalisee
      // qui doit se retrouver dans l'URL.
      return found.slug;
    } catch (raised) {
      reportFailure(raised);
      setSession({ ...initialSession, phase: "discovering" });
      return null;
    } finally {
      setBusy(false);
    }
  }, [reportFailure]);

  const signIn = useCallback(
    async (providerConfigId: string | null, providerType: string) => {
      if (!session.serviceEndpoint) return;
      setBusy(true);
      setError(null);
      try {
        const pkce = await createPkce();
        const redirect = redirectUri(window.location.origin);
        const client = new ApiClient({ baseUrl: session.serviceEndpoint });
        const started = await client.request<{
          flow_id: string;
          authorization_url: string;
        }>(`/api/auth/sso/${providerType}/start`, {
          method: "POST",
          body: {
            code_challenge: pkce.challenge,
            nonce: pkce.nonce,
            redirect_uri: redirect,
            machine: "nova-admin",
            provider_config_id: providerConfigId,
          },
        });
        rememberHandoff({
          kind: "sign_in",
          flowId: started.flow_id,
          verifier: pkce.verifier,
          redirect,
          providerType,
          organizationSlug: session.organizationSlug ?? "",
          serviceEndpoint: session.serviceEndpoint,
          organizationName: session.organizationName ?? "",
        });
        window.location.assign(started.authorization_url);
      } catch (raised) {
        reportFailure(raised);
        setBusy(false);
      }
    },
    [session.serviceEndpoint, reportFailure],
  );

  const startStepUp = useCallback(async () => {
    if (!session.serviceEndpoint || !session.userToken) return;
    setBusy(true);
    setError(null);
    try {
      const pkce = await createPkce();
      const redirect = redirectUri(window.location.origin);
      const client = new ApiClient({ baseUrl: session.serviceEndpoint });
      const plane = new ControlPlane(client);
      const started = await plane.startStepUp(session.userToken, {
        code_challenge: pkce.challenge,
        nonce: pkce.nonce,
        redirect_uri: redirect,
      });
      rememberHandoff({
        kind: "step_up",
        flowId: started.flow_id,
        verifier: pkce.verifier,
        redirect,
        // Le serveur choisit le fournisseur d'une réauthentification : il n'est
        // pas renvoyé, et la console n'a pas à le deviner.
        providerType: "",
        organizationSlug: session.organizationSlug ?? "",
        serviceEndpoint: session.serviceEndpoint,
        organizationName: session.organizationName ?? "",
        // Sans lui, le serveur ne saurait pas qui demande à s'élever.
        userToken: session.userToken,
      });
      window.location.assign(started.authorization_url);
    } catch (raised) {
      reportFailure(raised);
      setBusy(false);
    }
  }, [session.serviceEndpoint, session.userToken, reportFailure]);

  const completeAuthorization = useCallback(
    async (search: string) => {
      const response = readAuthorizationResponse(search);
      // Lu et vidé du même geste : une preuve ne sert qu'une fois, et un
      // rafraîchissement de la page de retour ne doit rien rejouer.
      const flow = consumeHandoff();
      if (!response || !flow) return;

      // La page a été rechargée par la redirection : la session est reconstruite
      // depuis le sas, sans refaire de découverte.
      setSession((current) => ({
        ...current,
        organizationSlug: flow.organizationSlug || current.organizationSlug,
        organizationName: flow.organizationName || current.organizationName,
        serviceEndpoint: flow.serviceEndpoint || current.serviceEndpoint,
        userToken: flow.userToken ?? current.userToken,
      }));

      if ("error" in response) {
        // Parcours annulé chez le fournisseur : ce n'est pas une panne, et cela
        // ne mérite pas un message alarmant.
        setBusy(false);
        setSession((current) => ({
          ...current,
          phase: flow.userToken ? "step_up_required" : "signed_out",
        }));
        return;
      }

      setBusy(true);
      try {
        const client = new ApiClient({ baseUrl: flow.serviceEndpoint });
        if (flow.kind === "sign_in") {
          const signedIn = await client.request<{ token: string }>(
            `/api/auth/sso/${flow.providerType}/exchange`,
            {
              method: "POST",
              body: {
                flow_id: flow.flowId,
                code: response.code,
                code_verifier: flow.verifier,
                redirect_uri: flow.redirect,
              },
            },
          );
          setSession((current) => ({
            ...current,
            phase: "step_up_required",
            userToken: signedIn.token,
          }));
        } else {
          const plane = new ControlPlane(client);
          const admin = await plane.completeStepUp(flow.userToken ?? "", {
            flow_id: flow.flowId,
            code: response.code,
            code_verifier: flow.verifier,
            redirect_uri: flow.redirect,
          });
          setSession((current) => ({
            ...current,
            phase: "administering",
            // Le jeton d'administration ne repasse jamais par le sas : il est
            // obtenu ici, et vit uniquement en mémoire.
            adminToken: admin.token,
            admin: {
              role: admin.role,
              capabilities: admin.capabilities,
              organizationId: admin.organization_id,
              expiresAt: Date.now() + admin.expires_in * 1000,
            },
          }));
        }
        setError(null);
      } catch (raised) {
        const apiError = raised instanceof ApiError ? raised : null;
        if (apiError?.kind === "forbidden") {
          // Rôle absent : ce n'est pas une erreur à réessayer.
          setSession((current) => ({ ...current, phase: "access_denied" }));
        } else {
          reportFailure(raised);
        }
      } finally {
        setBusy(false);
      }
    },
    [reportFailure],
  );

  const signOutAdmin = useCallback(async () => {
    if (control) {
      // L'échec n'empêche pas de fermer côté client : une session que l'on ne
      // sait plus révoquer ne doit pas rester affichée comme ouverte.
      try {
        await control.signOutAdmin();
      } catch {
        /* déjà expirée ou serveur injoignable */
      }
    }
    setSession((current) => ({
      ...current,
      phase: current.userToken ? "step_up_required" : "signed_out",
      admin: null,
      adminToken: null,
    }));
  }, [control]);

  const signOutCompletely = useCallback(async () => {
    clearHandoff();
    await signOutAdmin();
    if (session.serviceEndpoint && session.userToken) {
      try {
        await new ApiClient({ baseUrl: session.serviceEndpoint }).request(
          "/api/auth/logout",
          { method: "POST", body: {}, token: session.userToken },
        );
      } catch {
        /* sans conséquence : l'état local est effacé quoi qu'il arrive */
      }
    }
    setSession((current) => ({
      ...initialSession,
      phase: "signed_out",
      organizationSlug: current.organizationSlug,
      organizationName: current.organizationName,
      serviceEndpoint: current.serviceEndpoint,
    }));
  }, [signOutAdmin, session.serviceEndpoint, session.userToken]);

  const value: SessionContextValue = {
    session,
    control,
    error,
    busy,
    openOrganization,
    signIn,
    startStepUp,
    completeAuthorization,
    signOutAdmin,
    signOutCompletely,
    reportFailure,
  };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}
