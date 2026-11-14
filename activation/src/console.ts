// Console de l'éditeur (nova-console) : crée les codes d'activation depuis le
// navigateur et liste ceux déjà donnés. Protégée par Cloudflare Access ; le
// jeton d'Access est revérifié ici, pour qu'une erreur de réglage n'ouvre rien.

import { layout, type Ctx, type ConsoleEnv, type Page, type PageId } from "./shell";
import { PAGES } from "./pages";

export type { ConsoleEnv } from "./shell";

import { encoder, fromB64url } from "./b64";
export { claimsOf, invalid, newCode, signLicence, type LicenceRequest } from "./licence";

// ------------------------------------------------------------ Access

let certsCache: { domain: string; keys: JsonWebKey[]; at: number } | null = null;

async function accessKeys(domain: string, now: number, refresh = false): Promise<JsonWebKey[]> {
  const cached = certsCache && certsCache.domain === domain ? certsCache : null;
  // Après une rotation, un kid inconnu relit les certs, au plus une fois par minute.
  if (cached && now - cached.at < (refresh ? 60 : 3600)) return cached.keys;
  const response = await fetch(`https://${domain}/cdn-cgi/access/certs`);
  const keys = ((await response.json()) as { keys?: JsonWebKey[] }).keys;
  // Une réponse d'erreur n'est pas gardée : sinon tout le monde est refusé pendant une heure.
  if (!response.ok || !Array.isArray(keys)) return [];
  certsCache = { domain, keys, at: now };
  return keys;
}

/** L'adresse e-mail de la personne connectée par Access, ou null. */
export async function accessUser(request: Request, env: ConsoleEnv, now: number): Promise<string | null> {
  const token = request.headers.get("cf-access-jwt-assertion") ?? "";
  const [head, body, signature] = token.split(".");
  // Sans réglage d'Access, tout est refusé.
  if (!env.ACCESS_TEAM_DOMAIN || !env.ACCESS_AUD || !head || !body || !signature) return null;
  try {
    const header = JSON.parse(new TextDecoder().decode(fromB64url(head)));
    const claims = JSON.parse(new TextDecoder().decode(fromB64url(body)));
    const byKid = (keys: JsonWebKey[]) => keys.find((k) => (k as { kid?: string }).kid === header.kid);
    const keys = await accessKeys(env.ACCESS_TEAM_DOMAIN, now);
    const jwk = byKid(keys) ?? (keys.length ? byKid(await accessKeys(env.ACCESS_TEAM_DOMAIN, now, true)) : undefined);
    if (header.alg !== "RS256" || !jwk) return null;
    const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, fromB64url(signature), encoder.encode(`${head}.${body}`));
    const audiences = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (!ok || !audiences.includes(env.ACCESS_AUD) || !(claims.exp > now)) return null;
    return typeof claims.email === "string" ? claims.email : null;
  } catch {
    return null;
  }
}

// ------------------------------------------------------------ routage

const pageFor = (pathname: string): Page | undefined =>
  PAGES.find((p) => p.path === pathname || (p.path !== "/" && pathname.startsWith(`${p.path}/`)) || pathname === `${p.path}.csv`);

async function badges(env: ConsoleEnv): Promise<Partial<Record<PageId, number>>> {
  try {
    const q = await env.DB.prepare("SELECT COUNT(*) AS n FROM pilot_questions WHERE answer IS NULL").first<{ n: number }>();
    return { claude: q?.n ?? 0 };
  } catch {
    return {};
  }
}

export default {
  async fetch(request: Request, env: ConsoleEnv): Promise<Response> {
    const now = Math.floor(Date.now() / 1000);
    const user = await accessUser(request, env, now);
    if (!user) return new Response("Accès refusé", { status: 403 });
    const url = new URL(request.url);
    const ctx: Ctx = { env, user, now, url };
    if (request.method === "POST") {
      // Un formulaire posté depuis un autre site porterait le cookie d'Access : refusé.
      if (request.headers.get("origin") !== url.origin) return new Response("Origine refusée", { status: 403 });
      const action = PAGES.map((p) => p.actions?.[url.pathname]).find(Boolean);
      if (!action) return new Response("Introuvable", { status: 404 });
      const form = await request.formData().catch(() => null); // corps qui n'est pas un formulaire
      return form ? action(ctx, form) : new Response("Formulaire invalide", { status: 400 });
    }
    const page = request.method === "GET" ? pageFor(url.pathname) : undefined;
    if (!page) return new Response("Introuvable", { status: 404 });
    const [view, counts] = await Promise.all([page.render(ctx), badges(env)]);
    return view instanceof Response ? view : layout(ctx, page.id, view, counts, page.css);
  },
};
