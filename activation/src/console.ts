// Console de l'éditeur (nova-console) : crée les codes d'activation depuis le
// navigateur et liste ceux déjà donnés. Protégée par Cloudflare Access ; le
// jeton d'Access est revérifié ici, pour qu'une erreur de réglage n'ouvre rien.

import { dashboard, escape, type MetricsEnv } from "./dashboard";
import { normalizeCode, sha256Hex } from "./index";

export interface ConsoleEnv extends MetricsEnv {
  DB: D1Database;
  SIGNING_KEY: string; // clé privée Ed25519 (PKCS8, PEM), secret Cloudflare
  ACCESS_TEAM_DOMAIN: string; // ex. « nova-ecole.cloudflareaccess.com »
  ACCESS_AUD: string; // « Application Audience (AUD) Tag » de l'application Access
  GRAFANA_URL: string;
}

const CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ";
const DAY = 86400;
const KINDS = ["pilot", "subscription"];
const encoder = new TextEncoder();

const b64url = (bytes: Uint8Array) =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (text: string) =>
  Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

// ------------------------------------------------------------ Access

let certsCache: { domain: string; keys: JsonWebKey[]; at: number } | null = null;

async function accessKeys(domain: string, now: number): Promise<JsonWebKey[]> {
  if (certsCache && certsCache.domain === domain && now - certsCache.at < 3600) return certsCache.keys;
  const response = await fetch(`https://${domain}/cdn-cgi/access/certs`);
  const keys = ((await response.json()) as { keys: JsonWebKey[] }).keys;
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
    const jwk = (await accessKeys(env.ACCESS_TEAM_DOMAIN, now)).find((k) => (k as { kid?: string }).kid === header.kid);
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

// ------------------------------------------------------------ licence

export interface LicenceRequest {
  organization: string;
  tier: string;
  kind: string;
  starts: string;
  ends: string;
  ai_envelope_eur?: number;
}

// Refuse aussi le 31 février, que Date ferait glisser au 3 mars.
const isDay = (day: string) => {
  const parsed = new Date(`${day}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(day) && !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === day;
};

/** Mêmes règles que licence.issue() côté serveur ; null si tout va bien. */
export function invalid(r: LicenceRequest): string | null {
  if (!r.organization.trim() || r.organization.length > 100) return "Nom de l'établissement manquant.";
  if (!r.tier.trim() || r.tier.length > 40) return "Offre manquante.";
  if (!KINDS.includes(r.kind)) return "Type inconnu.";
  if (!isDay(r.starts) || !isDay(r.ends)) return "Dates au format AAAA-MM-JJ.";
  if (r.ends < r.starts) return "La fin est avant le début.";
  if (r.ai_envelope_eur !== undefined && !(r.ai_envelope_eur >= 0)) return "Enveloppe d'IA négative.";
  return null;
}

/** Jeton EdDSA lisible par PyJWT (licence.licence_state). */
export async function signLicence(pem: string, r: LicenceRequest): Promise<string> {
  const der = fromB64url(pem.replace(/-----[^-]+-----/g, "").replace(/\s+/g, ""));
  const key = await crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
  const claims: Record<string, unknown> = {
    organization: r.organization.trim(), tier: r.tier.trim(), kind: r.kind, starts: r.starts, ends: r.ends,
  };
  if (r.ai_envelope_eur !== undefined) claims.ai_envelope_eur = r.ai_envelope_eur;
  const input = `${b64url(encoder.encode(JSON.stringify({ alg: "EdDSA", typ: "JWT" })))}.${b64url(encoder.encode(JSON.stringify(claims)))}`;
  const signature = new Uint8Array(await crypto.subtle.sign("Ed25519", key, encoder.encode(input)));
  return `${input}.${b64url(signature)}`;
}

/** « NOVA-7K2P-91XQ », tiré sans biais (rejet des octets ≥ 248). */
export function newCode(): string {
  let chars = "";
  while (chars.length < 8) {
    for (const byte of crypto.getRandomValues(new Uint8Array(16))) {
      if (byte < 248 && chars.length < 8) chars += CODE_ALPHABET[byte % CODE_ALPHABET.length];
    }
  }
  return `NOVA-${chars.slice(0, 4)}-${chars.slice(4)}`;
}

const claimsOf = (licence: string): Record<string, unknown> => {
  try {
    return JSON.parse(new TextDecoder().decode(fromB64url(licence.split(".")[1])));
  } catch {
    return {};
  }
};

// ------------------------------------------------------------ page

const day = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toISOString().slice(0, 10) : "");

async function page(env: ConsoleEnv, user: string, now: number, notice = ""): Promise<Response> {
  const [{ results }, board] = await Promise.all([env.DB.prepare(
    "SELECT school, licence, expires_at, used_at FROM codes ORDER BY expires_at DESC LIMIT 200",
  ).all<{ school: string; licence: string; expires_at: number; used_at: number | null }>(), dashboard(env, now)]);
  const rows = results.map((row) => {
    const c = claimsOf(row.licence);
    const state = row.used_at ? `Activé le ${day(row.used_at)}` : row.expires_at > now ? `En attente (jusqu'au ${day(row.expires_at)})` : "Code expiré";
    return `<tr><td>${escape(row.school)}</td><td>${escape(c.tier)}</td><td>${escape(c.kind === "pilot" ? "Pilote" : "Abonnement")}</td><td>${escape(c.starts)} → ${escape(c.ends)}</td><td>${escape(state)}</td></tr>`;
  }).join("");
  const html = `<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console Nova</title><style>
:root{--bg:#f7f7f8;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--line:#e3e3e8;--accent:#3b6cf6}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1e1e21;--text:#f2f2f4;--muted:#a1a1a6;--line:#2e2e33;--accent:#7a9bff}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:20px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 12px}p{color:var(--muted);margin:0 0 16px}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
input,select{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text)}
button{font:inherit;padding:9px 16px;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer;align-self:end}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500}.scroll{overflow-x:auto}
.notice{background:var(--bg);border:1px solid var(--accent);border-radius:8px;padding:12px;margin-bottom:16px}
code{font-size:20px;letter-spacing:1px}a{color:var(--accent)}
h3{font-size:14px;margin:16px 0 8px}.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}
figure{margin:0}figcaption{font-size:13px;color:var(--muted);margin-bottom:4px}
.chart{width:100%;height:auto}.chart rect{fill:var(--accent)}.axis{font-size:11px;fill:var(--muted)}
.ok{color:#1f9d55}.bad{color:#d64545}.muted{color:var(--muted)}
</style></head><body><main>
<h1>Console Nova</h1><p>Connecté : ${escape(user)} · <a href="${escape(env.GRAFANA_URL)}/dashboards" target="_blank" rel="noopener">Graphiques (Grafana)</a></p>
${notice}
${board}
<section><h2>Nouveau code d'activation</h2><p>Le code sert une seule fois : la DSI le colle dans NOVA_ACTIVATION_CODE.</p>
<form method="post" action="/codes">
<label>Établissement<input name="organization" required maxlength="100"></label>
<label>Offre (tranche)<input name="tier" required maxlength="40" placeholder="151-250"></label>
<label>Type<select name="kind"><option value="pilot">Pilote</option><option value="subscription">Abonnement</option></select></label>
<label>Début<input name="starts" type="date" required></label>
<label>Fin<input name="ends" type="date" required></label>
<label>Enveloppe d'IA (€, facultatif)<input name="ai_envelope_eur" type="number" min="0" step="1"></label>
<label>Code valable (jours)<input name="code_days" type="number" min="1" max="90" value="30"></label>
<button type="submit">Créer le code</button></form></section>
<section><h2>Codes donnés</h2><div class="scroll"><table><thead><tr><th>Établissement</th><th>Offre</th><th>Type</th><th>Licence</th><th>État</th></tr></thead>
<tbody>${rows || `<tr><td colspan="5">Aucun code pour l'instant.</td></tr>`}</tbody></table></div></section>
</main></body></html>`;
  return new Response(html, {
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "x-frame-options": "DENY", "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'" },
  });
}

async function create(request: Request, env: ConsoleEnv, user: string, now: number): Promise<Response> {
  // Un formulaire posté depuis un autre site porterait le cookie d'Access : refusé.
  if (request.headers.get("origin") !== new URL(request.url).origin) return new Response("Origine refusée", { status: 403 });
  const form = await request.formData();
  const text = (name: string) => String(form.get(name) ?? "");
  const envelope = text("ai_envelope_eur").trim();
  const r: LicenceRequest = {
    organization: text("organization"), tier: text("tier"), kind: text("kind"), starts: text("starts"), ends: text("ends"),
    ...(envelope ? { ai_envelope_eur: Number(envelope) } : {}),
  };
  const days = Math.min(90, Math.max(1, Math.floor(Number(text("code_days")) || 30)));
  const problem = invalid(r);
  if (problem) return page(env, user, now, `<div class="notice">${escape(problem)}</div>`);
  if (!env.SIGNING_KEY) return page(env, user, now, `<div class="notice">Clé de signature absente (secret SIGNING_KEY).</div>`);
  const code = newCode();
  await env.DB.prepare("INSERT INTO codes (code_hash, licence, school, expires_at) VALUES (?, ?, ?, ?)")
    .bind(await sha256Hex(normalizeCode(code)), await signLicence(env.SIGNING_KEY, r), r.organization.trim(), now + days * DAY)
    .run();
  console.log(JSON.stringify({ event: "console.code_created", by: user, school: r.organization.trim() }));
  return page(env, user, now, `<div class="notice">Code pour ${escape(r.organization.trim())}, valable ${days} jours, à donner à la DSI :<br><code>${code}</code><br>Il ne sera plus affiché.</div>`);
}

export default {
  async fetch(request: Request, env: ConsoleEnv): Promise<Response> {
    const now = Math.floor(Date.now() / 1000);
    const user = await accessUser(request, env, now);
    if (!user) return new Response("Accès refusé", { status: 403 });
    const { pathname } = new URL(request.url);
    if (request.method === "GET" && pathname === "/") return page(env, user, now);
    if (request.method === "POST" && pathname === "/codes") return create(request, env, user, now);
    return new Response("Introuvable", { status: 404 });
  },
};
