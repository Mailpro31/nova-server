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
    const state = row.used_at
      ? `<span class="pill ok">Activé le ${day(row.used_at)}</span>`
      : row.expires_at > now ? `<span class="pill warn">En attente · jusqu'au ${day(row.expires_at)}</span>` : `<span class="pill neutral">Code expiré</span>`;
    return `<tr><td data-label="Établissement"><strong>${escape(row.school)}</strong></td><td data-label="Offre">${escape(c.tier)}</td><td data-label="Type">${escape(c.kind === "pilot" ? "Pilote" : "Abonnement")}</td><td data-label="Licence">${escape(c.starts)} → ${escape(c.ends)}</td><td data-label="État">${state}</td></tr>`;
  }).join("");
  const html = `<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#f4f5f7" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#181b21" media="(prefers-color-scheme: dark)">
<title>Console Nova</title><style>
:root{--bg:#f4f5f7;--card:#fff;--text:#16181d;--muted:#667085;--line:#e4e7ec;--accent:#4f6bed;--accent-soft:#eef1fe;
--ok:#12805c;--ok-soft:#e6f6ef;--warn:#b54708;--warn-soft:#fef4e6;--bad:#c4320a;--bad-soft:#fdeceb;--neutral-soft:#f0f1f4;--shadow:0 1px 2px rgba(16,24,40,.06)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#181b21;--text:#eef0f4;--muted:#98a2b3;--line:#262a33;--accent:#8ea2ff;--accent-soft:#1d2440;
--ok:#4ad295;--ok-soft:#10291f;--warn:#f7b267;--warn-soft:#2d2112;--bad:#ff8a73;--bad-soft:#331a16;--neutral-soft:#22262e;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.top{position:sticky;top:0;z-index:2;background:var(--card);border-bottom:1px solid var(--line)}
.top-in{max-width:1120px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.brand{font-weight:700;font-size:17px;display:flex;align-items:center;gap:8px}.brand i{width:22px;height:22px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#c9d4ff,var(--accent));display:inline-block}
nav{display:flex;gap:4px;flex-wrap:wrap;flex:1}nav a{color:var(--muted);text-decoration:none;padding:6px 10px;border-radius:8px;font-size:14px}nav a:hover{background:var(--accent-soft);color:var(--accent)}
.who{color:var(--muted);font-size:13px}.who a{color:var(--accent)}
main{max-width:1120px;margin:0 auto;padding:24px 16px 48px}
section{margin-bottom:36px;scroll-margin-top:72px}
h2{font-size:18px;margin:0 0 14px}h2 small{color:var(--muted);font-weight:400;font-size:13px;margin-left:6px}
h3{font-size:15px;margin:0}.lead{color:var(--muted);margin:-8px 0 14px}.muted,.empty{color:var(--muted)}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:12px;padding:18px;margin:0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.kpi,.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}
.kpi{padding:16px}.kpi-label{color:var(--muted);font-size:13px}.kpi-value{font-size:30px;font-weight:700;letter-spacing:-.02em;line-height:1.2;margin-top:4px}
.kpi-value small{font-size:16px;color:var(--muted);font-weight:500}.kpi-hint{margin-top:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.stack{display:grid;gap:14px}
.card{padding:18px}.card>header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:14px}
.stat{background:var(--bg);border-radius:10px;padding:10px 12px}.stat span{display:block;color:var(--muted);font-size:12px}.stat strong{font-size:20px}
dl{display:grid;grid-template-columns:auto 1fr;gap:8px 14px;margin:0;font-size:14px}dt{color:var(--muted)}dd{margin:0}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12.5px;font-weight:600;white-space:nowrap}
.pill.ok{background:var(--ok-soft);color:var(--ok)}.pill.warn{background:var(--warn-soft);color:var(--warn)}.pill.bad{background:var(--bad-soft);color:var(--bad)}.pill.neutral{background:var(--neutral-soft);color:var(--muted)}
.chip{display:inline-block;padding:1px 8px;border-radius:6px;background:var(--neutral-soft);font-size:12.5px;color:var(--muted)}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}
figure{margin:0}figcaption{font-size:13px;color:var(--muted);margin-bottom:6px}
.chart{width:100%;height:auto;display:block}.chart rect{fill:var(--accent);opacity:.55}.chart rect:last-of-type{opacity:1}.chart .base{stroke:var(--line)}.axis{font-size:11px;fill:var(--muted)}
.meter{height:10px;border-radius:999px;background:var(--neutral-soft);overflow:hidden;margin-bottom:14px}.meter div{height:100%;border-radius:999px;background:var(--ok)}
.meter.warn div{background:var(--warn)}.meter.bad div{background:var(--bad)}
.reports{list-style:none;padding:0;margin:0;display:grid;gap:12px}.reports header{justify-content:flex-start!important;margin-bottom:8px!important}
.reports time{margin-left:auto;color:var(--muted);font-size:13px}
blockquote{margin:0 0 10px;padding:10px 12px;background:var(--accent-soft);border-radius:10px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px}
label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:var(--muted);font-weight:500}
input,select{font:inherit;padding:9px 11px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--text)}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:1px}
button{font:inherit;font-weight:600;padding:10px 18px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer;align-self:end}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:13px}tr:last-child td{border-bottom:0}.scroll{overflow-x:auto}
.notice{background:var(--accent-soft);border:1px solid var(--accent);border-radius:14px;padding:16px;margin-bottom:24px}
code{font-size:24px;font-weight:700;letter-spacing:2px;display:inline-block;margin:6px 0;user-select:all}
/* Téléphone (iPhone 14 Pro : 393 px) : barre défilante, tuiles par deux, tableau en fiches, champs à 16 px (pas de zoom iOS). */
@media (max-width:640px){
.top-in{padding:10px max(16px,env(safe-area-inset-left)) 0;gap:6px}.who{order:2;width:100%;font-size:12px}
nav{order:3;flex:0 0 calc(100% + 32px);flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;margin:0 -16px;padding:4px 16px 8px}nav::-webkit-scrollbar{display:none}
nav a{white-space:nowrap;background:var(--neutral-soft)}
main{padding:16px max(16px,env(safe-area-inset-left)) calc(32px + env(safe-area-inset-bottom))}section{scroll-margin-top:120px;margin-bottom:28px}
.kpis{grid-template-columns:1fr 1fr;gap:10px}.kpi{padding:12px}.kpi-value{font-size:24px}.kpi:first-child{grid-column:1/-1}
.grid{grid-template-columns:1fr}.card{padding:14px}.stats{grid-template-columns:1fr 1fr}
input,select,button{font-size:16px}button{width:100%}
table,tbody,tr,td{display:block}thead{display:none}tr{padding:10px 0;border-bottom:1px solid var(--line)}tr:last-child{border-bottom:0}
td{border:0;padding:3px 0;display:flex;justify-content:space-between;gap:12px;text-align:right}td::before{content:attr(data-label);color:var(--muted);text-align:left}
td[colspan]{justify-content:center}td[colspan]::before{content:none}
code{font-size:20px}}
</style></head><body>
<div class="top"><div class="top-in"><div class="brand"><i></i>Console Nova</div>
<nav><a href="#apercu">Aperçu</a><a href="#ecoles">Écoles</a><a href="#usage">Usage</a><a href="#ia">Coûts d'IA</a><a href="#signalements">Signalements</a><a href="#licences">Licences</a></nav>
<div class="who">${escape(user)} · <a href="${escape(env.GRAFANA_URL)}/dashboards" target="_blank" rel="noopener">Grafana</a></div></div></div>
<main>
${notice}
${board}
<section id="licences"><h2>Licences</h2><div class="grid">
<article class="card"><header><h3>Nouveau code d'activation</h3></header><p class="muted">À usage unique : la DSI le colle dans NOVA_ACTIVATION_CODE.</p>
<form method="post" action="/codes">
<label>Établissement<input name="organization" required maxlength="100"></label>
<label>Offre (tranche)<input name="tier" required maxlength="40" placeholder="151-250"></label>
<label>Type<select name="kind"><option value="pilot">Pilote</option><option value="subscription">Abonnement</option></select></label>
<label>Début<input name="starts" type="date" required></label>
<label>Fin<input name="ends" type="date" required></label>
<label>Enveloppe d'IA (€, facultatif)<input name="ai_envelope_eur" type="number" min="0" step="1"></label>
<label>Code valable (jours)<input name="code_days" type="number" min="1" max="90" value="30"></label>
<button type="submit">Créer le code</button></form></article>
<article class="card"><header><h3>Codes donnés</h3></header><div class="scroll"><table><thead><tr><th>Établissement</th><th>Offre</th><th>Type</th><th>Licence</th><th>État</th></tr></thead>
<tbody>${rows || `<tr><td colspan="5" class="muted">Aucun code pour l'instant.</td></tr>`}</tbody></table></div></article>
</div></section>
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
