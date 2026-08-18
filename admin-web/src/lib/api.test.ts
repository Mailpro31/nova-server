/**
 * Client d'API : la traduction des échecs.
 *
 * C'est ce que la console fait de travers le plus facilement — un 401 confondu
 * avec un 403 envoie l'administrateur au mauvais écran.
 */

import { describe, expect, test } from "bun:test";
import { ApiClient, ApiError, SUPPORTED_CONTRACT_VERSION } from "./api";

/**
 * Remplace `fetch` par une reponse fixe.
 *
 * Le cast passe par `unknown` : le vrai type de `fetch` porte des membres
 * (`preconnect`, un corps en flux) dont le client n'utilise rien, et les
 * reproduire n'apprendrait rien a personne.
 */
function stubFetch(response: { status?: number; body?: string }) {
  globalThis.fetch = (async () => ({
    ok: response.status === undefined ? true : response.status < 400,
    status: response.status ?? 200,
    text: async () => response.body ?? "",
  })) as unknown as typeof fetch;
}

function client() {
  return new ApiClient({ baseUrl: "https://nova.example.com/", getToken: () => "jeton" });
}

async function kindOf(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
    return "ok";
  } catch (raised) {
    return raised instanceof ApiError ? raised.kind : "unexpected";
  }
}

describe("classification des échecs", () => {
  test("un serveur injoignable est un échec réseau", async () => {
    globalThis.fetch = (async () => {
      throw new TypeError("failed to fetch");
    }) as unknown as typeof fetch;
    expect(await kindOf(client().request("/x"))).toBe("network");
  });

  test("401 sans code est une absence d'authentification", async () => {
    stubFetch({ status: 401, body: "{}" });
    expect(await kindOf(client().request("/x"))).toBe("unauthenticated");
  });

  test("le code du serveur prime sur le statut", async () => {
    // 403 générique, mais le code dit qu'il faut se réauthentifier : la console
    // doit envoyer au step-up, pas afficher « permission refusée ».
    stubFetch({ status: 403, body: '{"detail":"ADMIN_STEP_UP_REQUIRED"}' });
    expect(await kindOf(client().request("/x"))).toBe("step_up_required");
  });

  test("une session expirée ou révoquée mène au même écran", async () => {
    for (const code of ["ADMIN_SESSION_EXPIRED", "ADMIN_SESSION_REVOKED"]) {
      stubFetch({ status: 401, body: `{"detail":"${code}"}` });
      expect(await kindOf(client().request("/x"))).toBe("admin_session_expired");
    }
  });

  test("un rôle insuffisant reste un refus, pas une déconnexion", async () => {
    stubFetch({ status: 403, body: '{"detail":"ADMIN_ROLE_REQUIRED"}' });
    expect(await kindOf(client().request("/x"))).toBe("forbidden");
  });

  test("404, 409, 400 et 500 sont distingués", async () => {
    const cases: [number, string][] = [
      [404, "not_found"],
      [409, "conflict"],
      [400, "invalid"],
      [422, "invalid"],
      [500, "server"],
    ];
    for (const [status, expected] of cases) {
      stubFetch({ status, body: "{}" });
      expect(await kindOf(client().request("/x"))).toBe(expected);
    }
  });

  test("un JSON illisible ne devient pas une donnée", async () => {
    stubFetch({ status: 200, body: "<html>oups</html>" });
    expect(await kindOf(client().request("/x"))).toBe("server");
  });

  test("un serveur plus récent est signalé, pas lu de travers", async () => {
    stubFetch({
      status: 200,
      body: `{"contract_version": ${SUPPORTED_CONTRACT_VERSION + 1}}`,
    });
    expect(await kindOf(client().request("/x"))).toBe("contract_mismatch");
  });

  test("un contrat connu passe", async () => {
    stubFetch({
      status: 200,
      body: `{"contract_version": ${SUPPORTED_CONTRACT_VERSION}, "ok": true}`,
    });
    expect(await kindOf(client().request("/x"))).toBe("ok");
  });
});

describe("transport", () => {
  test("le jeton part en en-tête, jamais en cookie ni en URL", async () => {
    let seen: { url: string; init: RequestInit } | null = null;
    globalThis.fetch = (async (url: string, init: RequestInit) => {
      seen = { url, init };
      return { ok: true, status: 200, text: async () => "{}" } as unknown as Response;
    }) as unknown as typeof fetch;

    await client().request("/api/control/organization");

    expect(seen!.url).toBe("https://nova.example.com/api/control/organization");
    expect((seen!.init.headers as Record<string, string>).Authorization).toBe(
      "Bearer jeton",
    );
    // Rien d'ambiant : c'est ce qui rend le CSRF sans objet sur ce chemin.
    expect(seen!.init.credentials).toBe("omit");
    expect(seen!.url).not.toContain("jeton");
  });

  test("un appel peut présenter explicitement un autre jeton", async () => {
    let headers: Record<string, string> = {};
    globalThis.fetch = (async (_url: string, init: RequestInit) => {
      headers = init.headers as Record<string, string>;
      return { ok: true, status: 200, text: async () => "{}" } as unknown as Response;
    }) as unknown as typeof fetch;

    // Pendant le step-up, seule la session utilisateur existe.
    await client().request("/api/admin/step-up/start", { token: "jeton-utilisateur" });
    expect(headers.Authorization).toBe("Bearer jeton-utilisateur");
  });
});
