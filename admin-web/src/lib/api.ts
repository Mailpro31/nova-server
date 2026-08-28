/**
 * Client du Control Plane.
 *
 * Un seul endroit sait parler au serveur : l'URL de base, la façon de porter le
 * jeton, la traduction des échecs. Des `fetch` dispersés dans les composants
 * finiraient par diverger — l'un oublierait le 401, l'autre afficherait une
 * trace brute.
 */

/** Version du contrat que cette console sait lire. */
export const SUPPORTED_CONTRACT_VERSION = 1;

/**
 * Causes d'échec, telles que l'interface a besoin de les distinguer.
 *
 * Le serveur renvoie des codes précis ; la console n'en garde que ce qui change
 * ce qu'elle doit *faire*. Afficher « HTTP 409 » à une DSI ne l'aide pas.
 */
export type ApiErrorKind =
  | "network"
  | "unauthenticated"
  | "step_up_required"
  | "admin_session_expired"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "invalid"
  | "contract_mismatch"
  | "server";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly code: string | null;
  readonly status: number;
  /**
   * Refus structuré, quand le serveur en renvoie un.
   *
   * Certains refus portent plus qu'un code : la validation d'un package nomme
   * le champ fautif et propose un message affichable. Le réduire à une chaîne
   * obligerait la console à dire « erreur » là où le serveur a pris la peine
   * d'expliquer.
   *
   * `null` pour les refus à code simple, qui restent la majorité.
   */
  readonly detail: Record<string, unknown> | null;

  constructor(
    kind: ApiErrorKind,
    status: number,
    code: string | null,
    detail: Record<string, unknown> | null = null,
  ) {
    super(code ?? kind);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function classify(status: number, code: string | null): ApiErrorKind {
  // Le code du serveur prime sur le statut : « session expirée » et « rôle
  // insuffisant » partagent parfois un statut mais n'appellent pas la même
  // réaction.
  switch (code) {
    case "ADMIN_STEP_UP_REQUIRED":
      return "step_up_required";
    case "ADMIN_SESSION_EXPIRED":
    case "ADMIN_SESSION_REVOKED":
      return "admin_session_expired";
    case "ADMIN_ROLE_REQUIRED":
      return "forbidden";
  }
  if (status === 401) return "unauthenticated";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "invalid";
  return "server";
}

export interface ApiClientOptions {
  /** Origine de l'API. Résolue par la découverte, jamais saisie par l'utilisateur. */
  baseUrl: string;
  /** Jeton courant, lu à chaque appel : il change au fil de la session. */
  getToken?: () => string | null;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getToken: () => string | null;

  constructor(options: ApiClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.getToken = options.getToken ?? (() => null);
  }

  async request<T>(
    path: string,
    init: { method?: string; body?: unknown; token?: string | null } = {},
  ): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    // `init.token` permet de présenter explicitement un autre jeton — la
    // session utilisateur pendant le step-up, par exemple, alors que le jeton
    // d'administration n'existe pas encore.
    const token = init.token !== undefined ? init.token : this.getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    if (init.body !== undefined) headers["Content-Type"] = "application/json";

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method: init.method ?? "GET",
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        // Aucun cookie : le jeton voyage dans un en-tête, et rien n'est envoyé
        // automatiquement par le navigateur.
        credentials: "omit",
      });
    } catch {
      // Serveur injoignable, DNS, TLS, hors ligne : indistinguables ici, et la
      // console n'a pas à spéculer.
      throw new ApiError("network", 0, null);
    }

    if (response.status === 204) return undefined as T;

    let payload: unknown = null;
    const raw = await response.text();
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch {
        // Une réponse illisible d'un serveur qui a répondu 200 est un défaut,
        // pas une donnée.
        throw new ApiError(response.ok ? "server" : "server", response.status, null);
      }
    }

    if (!response.ok) {
      const raw =
        payload && typeof payload === "object" && "detail" in payload
          ? (payload as { detail: unknown }).detail
          : null;
      // Un refus structuré porte son code dans `code` ; un refus simple *est*
      // son code. Les deux formes coexistent, et la console n'a pas à savoir
      // laquelle elle vient de recevoir.
      const structured =
        raw && typeof raw === "object" && !Array.isArray(raw)
          ? (raw as Record<string, unknown>)
          : null;
      const code = structured
        ? typeof structured.code === "string"
          ? structured.code
          : null
        : raw === null
          ? null
          : String(raw);
      throw new ApiError(
        classify(response.status, code),
        response.status,
        code,
        structured,
      );
    }

    const version =
      payload && typeof payload === "object" && "contract_version" in payload
        ? Number((payload as { contract_version: unknown }).contract_version)
        : null;
    if (version !== null && version > SUPPORTED_CONTRACT_VERSION) {
      // Un serveur plus récent que la console : mieux vaut le dire que lire de
      // travers des champs qu'on ne connaît pas.
      throw new ApiError("contract_mismatch", response.status, null);
    }

    return payload as T;
  }
}

/**
 * Résout l'adresse de service d'une organisation à partir de son identifiant.
 *
 * L'utilisateur ne saisit jamais d'adresse : il donne un identifiant
 * d'organisation, et la découverte (Phase 21) répond. Toutes les issues
 * négatives se ressemblent volontairement côté serveur — la console ne peut
 * donc pas distinguer « inconnue » de « suspendue », et ne le prétend pas.
 */
export async function discoverOrganization(
  controlUrl: string,
  slug: string,
): Promise<{ slug: string; displayName: string; serviceEndpoint: string }> {
  const client = new ApiClient({ baseUrl: controlUrl });
  const bootstrap = await client.request<{
    organization: { slug: string; display_name: string };
    service_endpoint: string;
  }>("/api/discovery/organization", { method: "POST", body: { organization: slug } });
  return {
    slug: bootstrap.organization.slug,
    displayName: bootstrap.organization.display_name,
    serviceEndpoint: bootstrap.service_endpoint,
  };
}
