// Pilotage de Claude depuis la console : pause, consignes, questions, PR retenues, journal.
// Claude lit et écrit ces tables à chaque réveil avec scripts/pilot.mjs (wrangler d1, compte Cloudflare).
// Règle : ce qui freine Claude (pause, PR retenue) s'applique tel quel ; ce qui l'autorise
// à une action sensible est reconfirmé par Sash dans le chat.
import { escape } from "./dashboard";
import { audit, empty, kpi, pill, redirect, when, type Ctx, type View } from "./shell";

const MAX = 4000;
const WEEK = 7 * 86400;
const TABS = ["questions", "consignes", "pr", "journal"] as const;
type Tab = (typeof TABS)[number];
type Tone = "ok" | "warn" | "bad" | "neutral" | "accent";
const RESULT: Record<string, [Tone, string]> = { ok: ["ok", "Réussi"], echec: ["bad", "Échec"], info: ["neutral", "Info"] };
const CI: Record<string, Tone> = { vert: "ok", rouge: "bad", "en cours": "warn" };
const multiline = (text: unknown) => escape(text).replace(/\n/g, "<br>");
/** Un lien venu des données n'est cliquable que s'il est en https:// ; sinon, texte simple. */
const link = (url: string, label = url) => /^https:\/\//i.test(url)
  ? `<a href="${escape(url)}" target="_blank" rel="noopener">${escape(label)}</a>` : escape(label);

export const RULE = "Ce qui freine Claude (pause, PR retenue) s'applique tel quel ; ce qui l'autorise à une action sensible est reconfirmé dans le chat.";

const count = async (db: D1Database, sql: string, ...args: unknown[]) => (await db.prepare(sql).bind(...args).first<{ n: number }>())?.n ?? 0;
const rows = async <T>(db: D1Database, sql: string, ...args: unknown[]) => (await db.prepare(sql).bind(...args).all<T>()).results;
const tabLink = (tab: Tab, extra: Record<string, string> = {}) => `/claude?${new URLSearchParams({ vue: tab, ...extra })}`;

export async function pilotView(ctx: Ctx): Promise<View> {
  const db = ctx.env.DB, since = ctx.now - WEEK;
  const [state, openQ, unread, prCount, heldCount, week, weekFail, journalTotal] = await Promise.all([
    db.prepare("SELECT value, at, by FROM pilot_state WHERE key = 'paused'").first<{ value: string; at: number; by: string }>(),
    count(db, "SELECT COUNT(*) AS n FROM pilot_questions WHERE answer IS NULL"),
    count(db, "SELECT COUNT(*) AS n FROM pilot_messages WHERE read_at IS NULL"),
    count(db, "SELECT COUNT(*) AS n FROM pilot_prs"),
    count(db, "SELECT COUNT(*) AS n FROM pilot_holds"),
    count(db, "SELECT COUNT(*) AS n FROM pilot_journal WHERE at >= ?", since),
    count(db, "SELECT COUNT(*) AS n FROM pilot_journal WHERE at >= ? AND result = 'echec'", since),
    count(db, "SELECT COUNT(*) AS n FROM pilot_journal"),
  ]);
  const asked = ctx.url.searchParams.get("vue") ?? "";
  const tab: Tab = (TABS as readonly string[]).includes(asked) ? (asked as Tab) : openQ ? "questions" : "consignes";

  const paused = state?.value === "1";
  const card = `<article class="card"><header><h3>Autonomie</h3>${paused ? pill("bad", "En pause") : pill("ok", "Active")}</header>
<p class="muted">${state ? `${paused ? "Mise en pause" : "Relancée"} le ${escape(when(state.at))} par ${escape(state.by)}.` : "Jamais mise en pause."}</p>
<p class="muted">${escape(RULE)}</p>
<form method="post" action="/pilot/pause" class="inline"><input type="hidden" name="paused" value="${paused ? "0" : "1"}">
<button type="submit" class="${paused ? "" : "danger"}">${paused ? "Relancer Claude" : "Mettre Claude en pause"}</button></form></article>`;
  const kpis = `<div class="kpis">${kpi("Questions à trancher", String(openQ), "", tabLink("questions"))}${kpi("Consignes non lues", String(unread), "", tabLink("consignes"))}`
    + `${kpi("PR ouvertes / retenues", `${prCount} / ${heldCount}`, "", tabLink("pr"))}${kpi("Actions sur 7 jours", String(week), weekFail ? pill("bad", `dont ${weekFail} échec${weekFail > 1 ? "s" : ""}`) : pill("ok", "aucun échec"), tabLink("journal"))}</div>`;

  const counts: Record<Tab, number> = { questions: openQ, consignes: unread, pr: prCount, journal: journalTotal };
  const labels: Record<Tab, string> = { questions: "Questions", consignes: "Consignes", pr: "PR", journal: "Journal" };
  const tabs = `<div class="toolbar"><nav class="seg" aria-label="Vues">${TABS.map((t) => `<a href="${tabLink(t)}" class="${t === tab ? "on" : ""}"${t === tab ? ' aria-current="page"' : ""}>${labels[t]} (${counts[t]})</a>`).join("")}</nav></div>`;

  const body = tab === "questions" ? await questionsTab(db) : tab === "consignes" ? await messagesTab(db) : tab === "pr" ? await prTab(db) : await journalTab(db, ctx.url);
  return { title: "Claude", subtitle: "Pause, consignes, questions, PR retenues et journal de la session autonome.", body: `<div class="top">${card}${kpis}</div>${tabs}${body}` };
}

async function questionsTab(db: D1Database): Promise<string> {
  const qs = await rows<{ id: number; at: number; title: string; details: string | null; answer: string | null; answered_at: number | null; answered_by: string | null }>(
    db, "SELECT * FROM pilot_questions ORDER BY answer IS NOT NULL, id DESC LIMIT 60");
  if (!qs.length) return empty("Aucune question de Claude.");
  return `<ul class="list">${qs.map((q) => `<li class="card"><header><strong>${escape(q.title)}</strong>${q.answer ? pill("ok", "Répondu") : pill("warn", "À trancher")}<time>${escape(when(q.at))}</time></header>
${q.details ? `<p>${multiline(q.details)}</p>` : ""}${q.answer
    ? `<blockquote>${multiline(q.answer)}</blockquote><p class="muted">Répondu par ${escape(q.answered_by ?? "?")}${q.answered_at ? ` le ${escape(when(q.answered_at))}` : ""}.</p>`
    : `<form method="post" action="/pilot/answer" class="stacked"><input type="hidden" name="id" value="${q.id}"><label>Votre réponse<textarea name="answer" required maxlength="${MAX}" rows="3"></textarea></label><button type="submit">Répondre</button></form>`}</li>`).join("")}</ul>`;
}

async function messagesTab(db: D1Database): Promise<string> {
  const ms = await rows<{ at: number; by: string; body: string; read_at: number | null; reply: string | null; replied_at: number | null }>(
    db, "SELECT * FROM pilot_messages ORDER BY id DESC LIMIT 30");
  const form = `<article class="card"><header><h3>Nouvelle consigne</h3></header><p class="muted">Lue au prochain réveil de Claude. ${escape(RULE)}</p>
<form method="post" action="/pilot/message" class="stacked"><label>Message<textarea name="body" required maxlength="${MAX}" rows="4" placeholder="Ex. : priorité aux bugs de la bulle cette semaine"></textarea></label><button type="submit">Envoyer à Claude</button></form></article>`;
  const list = ms.length ? `<ul class="list">${ms.map((m) => `<li class="card"><header><strong>${escape(m.by)}</strong>${
    m.replied_at ? pill("ok", "Traité") : m.read_at ? pill("neutral", `Lu le ${when(m.read_at)}`) : pill("warn", "Pas encore lu")}<time>${escape(when(m.at))}</time></header>
<blockquote>${multiline(m.body)}</blockquote>${m.reply ? `<p><strong>Claude :</strong> ${multiline(m.reply)}</p>` : ""}</li>`).join("")}</ul>` : empty("Aucune consigne envoyée.");
  return `<div class="stack">${form}${list}</div>`;
}

async function prTab(db: D1Database): Promise<string> {
  const [prs, holds] = await Promise.all([
    rows<{ repo: string; number: number; title: string; ci: string; url: string; seen_at: number }>(db, "SELECT * FROM pilot_prs ORDER BY repo, number"),
    rows<{ repo: string; number: number; reason: string | null }>(db, "SELECT * FROM pilot_holds"),
  ]);
  if (!prs.length) return empty("Aucune PR ouverte au dernier relevé.");
  const held = new Map(holds.map((h) => [`${h.repo}#${h.number}`, h]));
  const tr = prs.map((p) => {
    const h = held.get(`${p.repo}#${p.number}`);
    return `<tr><td data-label="PR">${link(p.url, `${p.repo} #${p.number}`)}</td><td data-label="Titre">${escape(p.title)}</td>
<td data-label="CI">${pill(CI[p.ci] ?? "neutral", p.ci)}</td><td data-label="Fusion">${h ? pill("bad", `Retenue${h.reason ? ` : ${h.reason}` : ""}`) : pill("ok", "Autorisée si verte")}</td>
<td data-label="Relevé"><span class="muted">relevé du ${escape(when(p.seen_at))}</span></td>
<td data-label=""><form method="post" action="/pilot/hold" class="inline"><input type="hidden" name="repo" value="${escape(p.repo)}"><input type="hidden" name="number" value="${p.number}"><input type="hidden" name="hold" value="${h ? "0" : "1"}">${h ? "" : `<input name="reason" maxlength="200" placeholder="Raison (facultatif)">`}<button type="submit" class="small ${h ? "" : "ghost"}">${h ? "Relâcher" : "Retenir"}</button></form></td></tr>`;
  }).join("");
  return `<article class="card"><div class="scroll"><table class="cards"><thead><tr><th>PR</th><th>Titre</th><th>CI</th><th>Fusion</th><th>Relevé</th><th></th></tr></thead><tbody>${tr}</tbody></table></div></article>`;
}

async function journalTab(db: D1Database, url: URL): Promise<string> {
  const asked = url.searchParams.get("resultat") ?? "";
  const resultat = ["echec", "ok", "info"].includes(asked) ? asked : "tous";
  const type = url.searchParams.get("type") ?? "";
  const where: string[] = [], args: unknown[] = [];
  if (resultat !== "tous") { where.push("result = ?"); args.push(resultat); }
  if (type) { where.push("kind = ?"); args.push(type); }
  const [entries, kinds] = await Promise.all([
    rows<{ at: number; kind: string; result: string; title: string; link: string | null; details: string | null }>(
      db, `SELECT * FROM pilot_journal ${where.length ? `WHERE ${where.join(" AND ")}` : ""} ORDER BY id DESC LIMIT 50`, ...args),
    rows<{ kind: string }>(db, "SELECT DISTINCT kind FROM pilot_journal ORDER BY kind"),
  ]);
  const href = (r: string, t: string) => tabLink("journal", { ...(r !== "tous" ? { resultat: r } : {}), ...(t ? { type: t } : {}) });
  const seg = (items: [string, string][], current: string, make: (v: string) => string) =>
    `<nav class="seg wrap">${items.map(([v, l]) => `<a href="${escape(make(v))}" class="${v === current ? "on" : ""}">${escape(l)}</a>`).join("")}</nav>`;
  const filters = `<div class="toolbar">${seg([["tous", "Tous"], ["echec", "Échecs"], ["ok", "Réussis"], ["info", "Infos"]], resultat, (v) => href(v, type))}</div>
<div class="toolbar">${seg([["", "Tous les types"], ...kinds.map((k): [string, string] => [k.kind, k.kind])], type, (v) => href(resultat, v))}</div>`;
  const list = entries.length ? `<ul class="list">${entries.map((j) => {
    const [tone, text] = RESULT[j.result] ?? ["neutral", j.result];
    return `<li class="card"><header><span class="chip">${escape(j.kind)}</span>${pill(tone, text)}<strong>${escape(j.title)}</strong><time>${escape(when(j.at))}</time></header>
${j.link ? `<p>${link(j.link)}</p>` : ""}${j.details ? `<details><summary>Rapport détaillé</summary><pre>${escape(j.details)}</pre></details>` : ""}</li>`;
  }).join("")}</ul>` : empty("Aucune action ne correspond.");
  return `${filters}${list}`;
}

/** Actions des formulaires ; chacune écrit une ligne console_audit puis redirige. null si ce n'est pas une action de pilotage. */
export async function pilotAction(path: string, form: FormData, db: D1Database, user: string, now: number): Promise<Response | null> {
  const text = (name: string) => String(form.get(name) ?? "").trim().slice(0, MAX);
  const run = (sql: string, ...args: unknown[]) => db.prepare(sql).bind(...args).run();
  let target = "/claude";
  if (path === "/pilot/pause") {
    const paused = text("paused") === "1";
    await run("INSERT OR REPLACE INTO pilot_state (key, value, at, by) VALUES ('paused', ?, ?, ?)", paused ? "1" : "0", now, user);
    await audit(db, user, now, paused ? "Claude mis en pause" : "Claude relancé");
  } else if (path === "/pilot/message") {
    if (!text("body")) return new Response("Message vide", { status: 400 });
    await run("INSERT INTO pilot_messages (at, by, body) VALUES (?, ?, ?)", now, user, text("body"));
    await audit(db, user, now, "Consigne envoyée", `${text("body").length} caractères`); // jamais le texte : il pourrait contenir un secret
    target = tabLink("consignes");
  } else if (path === "/pilot/answer") {
    if (!text("answer")) return new Response("Réponse vide", { status: 400 });
    const id = Number(text("id"));
    const done = await run("UPDATE pilot_questions SET answer = ?, answered_at = ?, answered_by = ? WHERE id = ? AND answer IS NULL", text("answer"), now, user, id);
    if (done.meta.changes) await audit(db, user, now, "Question répondue", `question n° ${id}`);
    target = tabLink("questions");
  } else if (path === "/pilot/hold") {
    const repo = text("repo"), number = Number(text("number"));
    if (!/^[\w.-]+$/.test(repo) || !Number.isInteger(number) || number <= 0) return new Response("PR invalide", { status: 400 });
    if (text("hold") === "1") {
      const reason = text("reason").slice(0, 200);
      await run("INSERT OR REPLACE INTO pilot_holds (repo, number, reason, at, by) VALUES (?, ?, ?, ?, ?)", repo, number, reason || null, now, user);
      await audit(db, user, now, `PR retenue ${repo} #${number}`, reason);
    } else {
      await run("DELETE FROM pilot_holds WHERE repo = ? AND number = ?", repo, number);
      await audit(db, user, now, `PR relâchée ${repo} #${number}`);
    }
    target = tabLink("pr");
  } else return null;
  console.log(JSON.stringify({ event: "console.pilot", action: path, by: user }));
  return redirect(target);
}
