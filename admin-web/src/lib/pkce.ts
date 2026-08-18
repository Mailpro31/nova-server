/**
 * PKCE et retour d'autorisation, côté navigateur.
 *
 * Le `code_verifier` ne quitte jamais cet onglet : c'est tout l'intérêt de
 * PKCE. Il vit le temps du parcours, dans une variable, et disparaît avec.
 */

function base64Url(bytes: ArrayBuffer): string {
  const binary = String.fromCharCode(...new Uint8Array(bytes));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(bytes = 48): string {
  const buffer = new Uint8Array(bytes);
  crypto.getRandomValues(buffer);
  return base64Url(buffer.buffer);
}

export interface PkcePair {
  verifier: string;
  challenge: string;
  nonce: string;
}

export async function createPkce(): Promise<PkcePair> {
  const verifier = randomString(48);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return { verifier, challenge: base64Url(digest), nonce: randomString(24) };
}

/**
 * Adresse de retour de l'autorisation.
 *
 * Le chemin est `/callback`, et ce n'est pas un choix libre : c'est celui que
 * l'App Registration Microsoft connait, parce que l'application de bureau
 * l'utilise deja. Entra compare l'URI de redirection **chemin compris** — un
 * `/auth/callback` invente ici produirait un AADSTS50011, et le corriger
 * demanderait de modifier l'enregistrement plutot que la console.
 *
 * L'origine doit par ailleurs etre litteralement `http://127.0.0.1:<port>` :
 * le serveur ecarte `localhost`, dont la resolution de noms ne se maitrise pas.
 */
export function redirectUri(origin: string): string {
  return `${origin.replace(/\/+$/, "")}/callback`;
}

/**
 * Lit le retour d'autorisation dans l'URL courante.
 *
 * Renvoie `null` si la page n'est pas un retour — un rafraîchissement ordinaire
 * ne doit rien déclencher.
 */
export function readAuthorizationResponse(
  search: string,
): { code: string } | { error: string } | null {
  const params = new URLSearchParams(search);
  const error = params.get("error");
  if (error) return { error };
  const code = params.get("code");
  return code ? { code } : null;
}
