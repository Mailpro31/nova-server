// Coque de la console, à la manière de Nova desktop : barre latérale par catégories,
// mêmes couleurs (src/styles/theme.css de nova-desktop), clair ou sombre selon le système.
// Aucune ligne de JavaScript côté page : la CSP l'interdit ; tout est lien, formulaire ou CSS.
import { escape, type MetricsEnv } from "./dashboard";

export interface ConsoleEnv extends MetricsEnv {
  DB: D1Database;
  SIGNING_KEY: string;        // clé privée Ed25519 (PKCS8, PEM), secret Cloudflare
  ACCESS_TEAM_DOMAIN: string; // ex. « nova-ecole.cloudflareaccess.com »
  ACCESS_AUD: string;         // « Application Audience (AUD) Tag » de l'application Access
  GRAFANA_URL: string;
}

/** Contexte d'une requête authentifiée. */
export interface Ctx {
  env: ConsoleEnv;
  user: string;   // e-mail vérifié par Cloudflare Access
  now: number;    // secondes Unix
  url: URL;
}

/** Ce qu'une page rend : un titre, un sous-titre facultatif, le contenu HTML (déjà échappé). */
export interface View { title: string; subtitle?: string; body: string; notice?: string }

export type PageId = "accueil" | "claude" | "ecoles" | "usage" | "ia" | "signalements" | "licences" | "activite" | "etat";

/** Une page de la console. `actions` : chemins POST (formulaires) qu'elle traite. */
export interface Page {
  id: PageId;
  path: string;                                    // « /ecoles » ; la page reçoit aussi « /ecoles/… »
  render(ctx: Ctx): Promise<View | Response>;
  actions?: Record<string, (ctx: Ctx, form: FormData) => Promise<Response>>;
  css?: string;                                    // CSS propre à la page, ajouté à la coque
}

// ------------------------------------------------------------ navigation

// Icônes Lucide (MIT), 24 × 24, trait — les mêmes que dans Nova desktop.
const ICON: Record<PageId, string> = {
  accueil: '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  claude: '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
  ecoles: '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2M10 6h4M10 10h4M10 14h4M10 18h4"/>',
  usage: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
  ia: '<path d="M4 10h12M4 14h9"/><path d="M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12c0 4.4 3.5 8 7.8 8 2 0 3.8-.8 5.2-2"/>',
  signalements: '<circle cx="12" cy="12" r="10"/><path d="m4.93 4.93 4.24 4.24M14.83 9.17l4.24-4.24M14.83 14.83l4.24 4.24M9.17 14.83l-4.24 4.24"/><circle cx="12" cy="12" r="4"/>',
  licences: '<path d="M2.586 17.414A2 2 0 0 0 2 18.828V21a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h1a1 1 0 0 0 1-1v-1a1 1 0 0 1 1-1h.172a2 2 0 0 0 1.414-.586l.814-.814a6.5 6.5 0 1 0-4-4z"/><circle cx="16.5" cy="7.5" r=".5" fill="currentColor"/>',
  activite: '<path d="M15 12h-5M15 8h-5M19 17V5a2 2 0 0 0-2-2H4"/><path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>',
  etat: '<path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>',
};
export const icon = (id: PageId, size = 18) =>
  `<svg class="ico" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICON[id]}</svg>`;

/** La barre latérale : catégories et pages, dans l'ordre d'affichage. */
export const NAV: { group: string; items: { id: PageId; label: string; href: string }[] }[] = [
  { group: "Pilotage", items: [
    { id: "accueil", label: "Accueil", href: "/" },
    { id: "claude", label: "Claude", href: "/claude" },
  ] },
  { group: "Écoles", items: [
    { id: "ecoles", label: "Écoles", href: "/ecoles" },
    { id: "usage", label: "Usage", href: "/usage" },
    { id: "ia", label: "Coûts d'IA", href: "/ia" },
  ] },
  { group: "Support", items: [
    { id: "signalements", label: "Signalements", href: "/signalements" },
  ] },
  { group: "Commercial", items: [
    { id: "licences", label: "Licences", href: "/licences" },
  ] },
  { group: "Système", items: [
    { id: "activite", label: "Activité", href: "/activite" },
    { id: "etat", label: "État des services", href: "/etat" },
  ] },
];

// ------------------------------------------------------------ petits composants partagés

export const pill = (tone: "ok" | "warn" | "bad" | "neutral" | "accent", text: string) => `<span class="pill ${tone}">${escape(text)}</span>`;
export const when = (s: number) => new Date(s * 1000).toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "medium", timeStyle: "short" });
/** Détail d'une ligne du journal : une clé de signalement « école:horodatage en ms » devient « école · date de Paris ». */
export const auditDetail = (action: string, detail: string | null): string => {
  const m = action.startsWith("Signalement") ? /^(.+):(\d{10,})$/.exec(detail ?? "") : null;
  return m ? `${m[1]} · ${when(Number(m[2]) / 1000)}` : detail ?? "";
};
export const kpi = (label: string, value: string, hint = "", href = "") =>
  `<${href ? `a href="${escape(href)}"` : "div"} class="kpi">${`<div class="kpi-label">${escape(label)}</div><div class="kpi-value">${value}</div>${hint ? `<div class="kpi-hint">${hint}</div>` : ""}`}</${href ? "a" : "div"}>`;
export const stat = (label: string, value: string) => `<div class="stat"><span>${escape(label)}</span><strong>${value}</strong></div>`;
/** Une ligne compacte de journal (accueil et activité) : contenu à gauche, heure à droite. `html` est déjà échappé. */
export const eventRow = (html: string, time: string) => `<div class="row-link"><div class="grow">${html}</div><time class="muted">${escape(time)}</time></div>`;
export const empty = (text: string) => `<p class="empty">${escape(text)}</p>`;
export const notice = (text: string, tone: "ok" | "bad" | "accent" = "accent") => `<div class="notice ${tone}">${text}</div>`;

/** Trace dans le journal d'activité de la console : qui a fait quoi, jamais de secret ni de code en clair. */
export async function audit(db: D1Database, user: string, now: number, action: string, detail = ""): Promise<void> {
  await db.prepare("INSERT INTO console_audit (at, by, action, detail) VALUES (?, ?, ?, ?)").bind(now, user, action, detail.slice(0, 500)).run();
}

export const redirect = (location: string) => new Response(null, { status: 303, headers: { location } });

const HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "cache-control": "no-store",
  "x-frame-options": "DENY",
  // same-origin et non no-referrer : avec no-referrer, Chrome envoie « Origin: null » sur les formulaires, refusés.
  "referrer-policy": "same-origin",
  "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
};

/** Page complète : barre latérale + en-tête + contenu. `badges` : petits compteurs à côté d'une entrée. */
export function layout(ctx: Ctx, active: PageId, view: View, badges: Partial<Record<PageId, number>>, pageCss = ""): Response {
  const nav = NAV.map((g) => `<div class="nav-group"><div class="nav-title">${escape(g.group)}</div>${g.items.map((it) =>
    `<a href="${it.href}" class="nav-item${it.id === active ? " active" : ""}"${it.id === active ? ' aria-current="page"' : ""}>${icon(it.id)}<span>${escape(it.label)}</span>${badges[it.id] ? `<b class="badge">${badges[it.id]}</b>` : ""}</a>`).join("")}</div>`).join("");
  const html = `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#1f1f22" media="(prefers-color-scheme: dark)">
<title>${escape(view.title)} · Console Nova</title><style>${CSS}${pageCss}</style></head><body>
<div class="app"><aside class="side"><div class="brand"><i class="orb"></i><span>Console Nova</span></div>
<nav aria-label="Sections">${nav}</nav>
<div class="side-foot"><span class="who" title="${escape(ctx.user)}">${escape(ctx.user)}</span><a href="${escape(ctx.env.GRAFANA_URL)}/dashboards" target="_blank" rel="noopener">Grafana ↗</a></div></aside>
<main class="main"><header class="head"><h1>${escape(view.title)}</h1>${view.subtitle ? `<p class="sub">${view.subtitle}</p>` : ""}</header>
${view.notice ?? ""}${view.body}</main></div></body></html>`;
  return new Response(html, { headers: HEADERS });
}

// ------------------------------------------------------------ styles

const CSS = `
:root{color-scheme:light dark;
--window:#f5f5f7;--sidebar:#ececee;--surface:#fff;--inset:#f0f0f2;--text:#1d1d1f;--muted:#636366;--line:rgba(0,0,0,.08);--line-strong:rgba(0,0,0,.14);
--accent:#0066cc;--accent-hover:#004fa3;--link:#0066cc;--ok:#1b7a32;--warn:#a34800;--bad:#cc0014;--bad-fill:#cc0014;--ultra:#7a5cba;
--ok-soft:rgba(36,138,61,.12);--warn-soft:rgba(178,80,0,.12);--bad-soft:rgba(215,0,21,.10);--accent-soft:rgba(10,132,255,.10);--neutral-soft:rgba(0,0,0,.05);
--r-chip:7px;--r-control:9px;--r-card:13px;--shadow:0 1px 2px rgba(0,0,0,.06);--shadow-float:0 8px 24px -8px rgba(0,0,0,.18);--ease:cubic-bezier(.2,.8,.2,1)}
@media (prefers-color-scheme:dark){:root{
--window:#1f1f22;--sidebar:#28282c;--surface:#2c2c30;--inset:#1a1a1d;--text:#f2f2f4;--muted:#a8a8b0;--line:rgba(255,255,255,.07);--line-strong:rgba(255,255,255,.16);
--accent:#0a6fe0;--accent-hover:#0858bd;--link:#5aaaff;--bad-fill:#d7271c;--ok:#30d158;--warn:#ff9f0a;--bad:#ff7a70;--ultra:#c9b6f0;
--ok-soft:rgba(48,209,88,.14);--warn-soft:rgba(255,159,10,.14);--bad-soft:rgba(255,69,58,.14);--accent-soft:rgba(10,132,255,.16);--neutral-soft:rgba(255,255,255,.06);--shadow:none}}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{overflow-wrap:anywhere;background:var(--window);color:var(--text);font:14px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,-apple-system,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--link);text-decoration:none}a:hover{text-decoration:underline}
.app{display:flex;min-height:100vh}
.side{position:sticky;top:0;height:100vh;width:220px;flex-shrink:0;background:var(--sidebar);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:16px 12px;gap:4px;overflow-y:auto}
.brand{display:flex;align-items:center;gap:10px;padding:0 10px 16px;font-weight:600;font-size:15px;letter-spacing:-.01em}
.orb{width:22px;height:22px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#bfe0ff,var(--accent) 60%,#0057c2);box-shadow:0 0 12px rgba(10,132,255,.45);flex-shrink:0}
.nav-group{display:flex;flex-direction:column;gap:2px;margin-bottom:10px}
.nav-title{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:6px 10px 4px}
.nav-item{display:flex;align-items:center;gap:10px;height:34px;padding:0 10px;border-radius:var(--r-chip);color:var(--muted);font-size:14px;transition:background-color 120ms var(--ease),color 120ms var(--ease)}
.nav-item:hover{background:var(--neutral-soft);color:var(--text);text-decoration:none}
.nav-item.active{background:var(--neutral-soft);color:var(--text);font-weight:600}.nav-item.active .ico{color:var(--accent)}
.nav-item span{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.badge{min-width:20px;height:20px;padding:0 6px;border-radius:999px;background:var(--accent);color:#fff;font-size:11.5px;display:grid;place-items:center}
.ico{flex-shrink:0}
.side-foot{margin-top:auto;padding:12px 10px 0;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:4px;font-size:12px}
.who{color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.main{flex:1;min-width:0;padding:28px 32px 56px;max-width:1180px}
.head{margin-bottom:22px}.head h1{font-size:24px;letter-spacing:-.02em;margin:0}.sub{color:var(--muted);margin:4px 0 0}
section{margin-bottom:32px}h2{font-size:16px;margin:0 0 12px;letter-spacing:-.01em}h2 small,h3 small{color:var(--muted);font-weight:400;font-size:13px;margin-left:6px}
h3{font-size:14.5px;margin:0}.muted{color:var(--muted)}.lead{color:var(--muted);margin:-6px 0 14px}
.card,.kpi{background:var(--surface);border:1px solid var(--line);border-radius:var(--r-card);box-shadow:var(--shadow)}
.card{padding:18px}.card>header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:14px}.stack,.list{grid-template-columns:minmax(0,1fr)}.stack{display:grid;gap:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:28px}
.kpi{display:block;padding:16px;color:inherit}a.kpi:hover{text-decoration:none;border-color:var(--line-strong)}
.kpi-label{color:var(--muted);font-size:12.5px}.kpi-value{font-size:28px;font-weight:700;letter-spacing:-.02em;line-height:1.2;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi-value small{font-size:15px;color:var(--muted);font-weight:500}.kpi-hint{margin-top:8px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:12px}
.stat{background:var(--inset);border-radius:var(--r-control);padding:10px 12px}.stat span{display:block;color:var(--muted);font-size:12px}.stat strong{font-size:19px;font-variant-numeric:tabular-nums}
dl{display:grid;grid-template-columns:auto 1fr;gap:8px 14px;margin:0}dt{color:var(--muted)}dd{margin:0}
.pill{display:inline-block;max-width:100%;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.pill.ok{background:var(--ok-soft);color:var(--ok)}.pill.warn{background:var(--warn-soft);color:var(--warn)}.pill.bad{background:var(--bad-soft);color:var(--bad)}
.pill.neutral{background:var(--neutral-soft);color:var(--muted)}.pill.accent{background:var(--accent-soft);color:var(--link)}
.chip{display:inline-block;padding:1px 8px;border-radius:6px;background:var(--neutral-soft);font-size:12px;color:var(--muted)}.chips{display:flex;gap:6px;flex-wrap:wrap}
.empty{background:var(--surface);border:1px dashed var(--line-strong);border-radius:var(--r-card);padding:18px;margin:0;color:var(--muted)}
.notice{border-radius:var(--r-card);padding:14px 16px;margin-bottom:22px;border:1px solid var(--accent);background:var(--accent-soft)}
.notice.ok{border-color:var(--ok);background:var(--ok-soft)}.notice.bad{border-color:var(--bad);background:var(--bad-soft)}
code.big{font-size:24px;font-weight:700;letter-spacing:2px;display:inline-block;margin:6px 0;user-select:all}
.list{list-style:none;padding:0;margin:0;display:grid;gap:10px}.list>li>header{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.list time{margin-left:auto;color:var(--muted);font-size:12.5px}
blockquote{margin:0 0 10px;padding:10px 12px;background:var(--inset);border-radius:var(--r-control)}
pre{margin:8px 0 0;padding:12px;background:var(--inset);border:1px solid var(--line);border-radius:var(--r-control);font-size:12.5px;line-height:1.45;white-space:pre-wrap;word-break:break-word;max-height:480px;overflow:auto}
details summary{cursor:pointer;color:var(--link);padding:2px 0;font-weight:600;font-size:13.5px}
form{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px}form.stacked{grid-template-columns:1fr}
form.inline{display:flex;gap:8px;align-items:center;flex-wrap:wrap}form.inline input{flex:1;min-width:120px}
label{display:flex;flex-direction:column;gap:6px;font-size:12.5px;color:var(--muted);font-weight:500}
input,select,textarea{font:inherit;font-size:14px;height:2.5rem;padding:0 11px;border:1px solid var(--line-strong);border-radius:var(--r-control);background:var(--inset);color:var(--text)}
textarea{height:auto;padding:9px 11px;resize:vertical;width:100%}
::placeholder{color:var(--muted);opacity:1}
:focus-visible{outline:2px solid var(--link);outline-offset:2px}
button,.btn{font:inherit;font-size:14px;font-weight:600;height:2.5rem;padding:0 16px;border:0;border-radius:var(--r-control);background:var(--accent);color:#fff;cursor:pointer;align-self:end;display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap}
button:hover,.btn:hover{background:var(--accent-hover);text-decoration:none}
button.ghost,.btn.ghost{background:var(--neutral-soft);color:var(--text)}button.ghost:hover,.btn.ghost:hover{background:var(--line-strong)}
button.danger{background:var(--bad-fill)}button.small,.btn.small{height:2rem;padding:0 12px;font-size:13px}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.seg{display:inline-flex;flex-wrap:wrap;max-width:100%;background:var(--neutral-soft);border-radius:var(--r-control);padding:3px;gap:2px}
.seg a{padding:5px 12px;border-radius:7px;color:var(--muted);font-size:13px}.seg a:hover{text-decoration:none;color:var(--text)}.seg a.on{background:var(--surface);color:var(--text);font-weight:600;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--muted);font-weight:500;font-size:12.5px}tr:last-child td{border-bottom:0}.scroll{overflow-x:auto}
.meter{height:8px;border-radius:999px;background:var(--neutral-soft);overflow:hidden;margin-bottom:12px}.meter div{height:100%;border-radius:999px;background:var(--ok)}
.meter.warn div{background:var(--warn)}.meter.bad div{background:var(--bad)}
.axis{font-size:12px;color:var(--muted)}.ends{display:flex;justify-content:space-between;margin-top:6px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:20px}figure{margin:0}figcaption{font-size:12.5px;color:var(--muted);margin-bottom:6px}
.cols{position:relative;display:flex;align-items:flex-end;gap:0;height:130px;margin-top:30px;border-bottom:1px solid var(--line)}
.bars{max-width:max(calc(var(--n,14)*56px),180px)}
.col{all:unset;position:relative;flex:1 1 0;max-width:56px;height:100%;display:flex;align-items:flex-end;cursor:pointer;border-radius:4px}
.col i{display:block;width:calc(100% - 4px);margin:0 auto;background:var(--accent);opacity:.5;border-radius:4px 4px 0 0;transition:opacity 120ms}
.col:last-child i{opacity:.85}.cols:hover .col i{opacity:.25}.cols .col:hover i,.col:focus i{opacity:1}
.col:hover,.col:focus{background:var(--neutral-soft)}.col:focus-visible{outline:2px solid var(--link)}
.tip{display:none;position:absolute;bottom:calc(100% + 6px);left:50%;transform:translateX(-50%);z-index:3;white-space:nowrap;pointer-events:none;background:var(--text);color:var(--surface);font-size:12px;line-height:1.3;padding:6px 10px;border-radius:8px;box-shadow:var(--shadow-float)}
.tip b{display:block;font-size:15px}.col.l .tip{left:0;transform:none}.col.r .tip{left:auto;right:0;transform:none}.col:hover .tip,.col:focus .tip{display:block}
.row-link{display:flex;align-items:center;gap:12px;padding:12px 14px;border-bottom:1px solid var(--line);color:inherit}.row-link:last-child{border-bottom:0}
.row-link:hover{background:var(--neutral-soft);text-decoration:none}.row-link .grow{flex:1;min-width:0}.row-link time{flex-shrink:0;white-space:nowrap;font-size:12.5px}.row-link .chev{color:var(--muted)}
.card.flush{padding:0;overflow:hidden}
/* Téléphone (iPhone 14 Pro, 393 px) : la barre latérale devient une barre d'onglets défilante en haut. */
@media (max-width:760px){
.app{flex-direction:column}
.side{position:sticky;top:0;z-index:5;height:auto;width:auto;flex-direction:row;align-items:center;padding:8px max(12px,env(safe-area-inset-left));gap:8px;overflow-x:auto;border-right:0;border-bottom:1px solid var(--line);scrollbar-width:none}
.side::-webkit-scrollbar{display:none}.brand{padding:0 6px 0 0}.brand span,.nav-title,.side-foot{display:none}
nav{display:flex;gap:4px}.nav-group{flex-direction:row;margin:0}.nav-item{height:34px;white-space:nowrap}.nav-item span{overflow:visible}
.main{padding:18px max(12px,env(safe-area-inset-left)) calc(40px + env(safe-area-inset-bottom))}.head h1{font-size:21px}
.kpis{grid-template-columns:1fr 1fr;gap:10px}.kpi{padding:12px}.kpi-value{font-size:22px}
.grid{grid-template-columns:1fr}.card{padding:12px}.stats{grid-template-columns:1fr 1fr}
input,select,textarea,button{font-size:16px}form button{width:100%}
table.cards,table.cards tbody,table.cards tr,table.cards td{display:block}table.cards thead{display:none}
table.cards tr{padding:10px 0;border-bottom:1px solid var(--line)}table.cards tr:last-child{border-bottom:0}
table.cards td{border:0;padding:3px 0;display:flex;justify-content:space-between;gap:12px;text-align:right}
table.cards td a{display:inline-block;padding:2px 0}
table.cards td::before{content:attr(data-label);color:var(--muted);text-align:left}table.cards td[colspan]{justify-content:center}table.cards td[colspan]::before{content:none}}
`;
