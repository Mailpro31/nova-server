// Pilotage de Claude depuis la console : pause, consignes, questions, PR retenues, journal.
// Claude lit et écrit ces tables à chaque réveil avec scripts/pilot.mjs (wrangler d1, compte Cloudflare).
// Règle : ce qui freine Claude (pause, PR retenue) s'applique tel quel ; ce qui l'autorise
// à une action sensible est reconfirmé par Sash dans le chat.
import { escape } from "./dashboard";

const MAX = 4000;
const when = (s: number) => new Date(s * 1000).toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "medium", timeStyle: "short" });
const pill = (tone: string, text: string) => `<span class="pill ${tone}">${escape(text)}</span>`;
const RESULT: Record<string, [string, string]> = { ok: ["ok", "Réussi"], echec: ["bad", "Échec"], info: ["neutral", "Info"] };
const CI: Record<string, string> = { vert: "ok", rouge: "bad", "en cours": "warn" };
const multiline = (text: unknown) => escape(text).replace(/\n/g, "<br>");

type Rows<T> = Promise<{ results: T[] }>;

export async function pilotSection(db: D1Database, now: number): Promise<string> {
  const all = <T>(sql: string) => db.prepare(sql).all<T>() as unknown as Rows<T>;
  const [state, messages, questions, prs, holds, journal] = await Promise.all([
    db.prepare("SELECT value, at, by FROM pilot_state WHERE key = 'paused'").first<{ value: string; at: number; by: string }>(),
    all<{ id: number; at: number; by: string; body: string; read_at: number | null; reply: string | null; replied_at: number | null }>(
      "SELECT * FROM pilot_messages ORDER BY id DESC LIMIT 20"),
    all<{ id: number; at: number; title: string; details: string | null; answer: string | null; answered_at: number | null }>(
      "SELECT * FROM pilot_questions ORDER BY answer IS NOT NULL, id DESC LIMIT 30"),
    all<{ repo: string; number: number; title: string; ci: string; url: string; seen_at: number }>("SELECT * FROM pilot_prs ORDER BY repo, number"),
    all<{ repo: string; number: number; reason: string | null; by: string }>("SELECT * FROM pilot_holds"),
    all<{ id: number; at: number; kind: string; result: string; title: string; link: string | null; details: string | null }>(
      "SELECT * FROM pilot_journal ORDER BY id DESC LIMIT 50"),
  ]);
  const paused = state?.value === "1";
  const pause = `<article class="card"><header><h3>Autonomie</h3>${paused ? pill("bad", "En pause") : pill("ok", "Active")}</header>
<p class="muted">${state ? `${paused ? "Mise en pause" : "Relancée"} le ${escape(when(state.at))} par ${escape(state.by)}.` : "Jamais mise en pause."}
En pause, Claude ne fusionne, ne déploie, ne publie et n'écrit au PC fixe rien du tout ; il lit seulement cette page.</p>
<form method="post" action="/pilot/pause" class="inline"><input type="hidden" name="paused" value="${paused ? "0" : "1"}">
<button type="submit" class="${paused ? "" : "danger"}">${paused ? "Relancer Claude" : "Mettre Claude en pause"}</button></form></article>`;

  const open = questions.results.filter((q) => !q.answer).length;
  const questionList = questions.results.map((q) => `<li class="card"><header><strong>${escape(q.title)}</strong>${q.answer ? pill("ok", "Répondu") : pill("warn", "À trancher")}<time>${escape(when(q.at))}</time></header>
${q.details ? `<p>${multiline(q.details)}</p>` : ""}${q.answer
    ? `<blockquote>${multiline(q.answer)}</blockquote>`
    : `<form method="post" action="/pilot/answer"><input type="hidden" name="id" value="${q.id}"><label>Votre réponse<textarea name="answer" required maxlength="${MAX}" rows="2"></textarea></label><button type="submit">Répondre</button></form>`}</li>`).join("");

  const messageList = messages.results.map((m) => `<li class="card"><header><strong>${escape(m.by)}</strong>${m.replied_at ? pill("ok", "Traité") : m.read_at ? pill("neutral", "Lu") : pill("warn", "Pas encore lu")}<time>${escape(when(m.at))}</time></header>
<blockquote>${multiline(m.body)}</blockquote>${m.reply ? `<p><strong>Claude :</strong> ${multiline(m.reply)}</p>` : ""}</li>`).join("");

  const held = new Map(holds.results.map((h) => [`${h.repo}#${h.number}`, h]));
  const snapshot = prs.results[0]?.seen_at;
  const prRows = prs.results.map((p) => {
    const h = held.get(`${p.repo}#${p.number}`);
    return `<tr><td data-label="PR"><a href="${escape(p.url)}" target="_blank" rel="noopener">${escape(p.repo)} #${p.number}</a></td><td data-label="Titre">${escape(p.title)}</td>
<td data-label="CI">${pill(CI[p.ci] ?? "neutral", p.ci)}</td><td data-label="Fusion">${h ? pill("bad", `Retenue${h.reason ? ` : ${h.reason}` : ""}`) : pill("ok", "Autorisée si verte")}</td>
<td data-label=""><form method="post" action="/pilot/hold" class="inline"><input type="hidden" name="repo" value="${escape(p.repo)}"><input type="hidden" name="number" value="${p.number}"><input type="hidden" name="hold" value="${h ? "0" : "1"}">${h ? "" : `<input name="reason" maxlength="200" placeholder="Raison (facultatif)">`}<button type="submit" class="${h ? "" : "ghost"}">${h ? "Relâcher" : "Retenir"}</button></form></td></tr>`;
  }).join("");

  const journalList = journal.results.map((j) => {
    const [tone, label] = RESULT[j.result] ?? ["neutral", j.result];
    return `<li class="card"><header><span class="chip">${escape(j.kind)}</span>${pill(tone, label)}<strong>${escape(j.title)}</strong><time>${escape(when(j.at))}</time></header>
${j.link ? `<p><a href="${escape(j.link)}" target="_blank" rel="noopener">${escape(j.link)}</a></p>` : ""}${j.details ? `<details><summary>Rapport détaillé</summary><pre>${escape(j.details)}</pre></details>` : ""}</li>`;
  }).join("");

  return `<section id="claude"><h2>Claude <small>${open ? `${open} question${open > 1 ? "s" : ""} en attente` : "rien en attente"}</small></h2>
<div class="grid">${pause}
<article class="card"><header><h3>Consigne pour Claude</h3></header><p class="muted">Lue au prochain réveil. Une consigne qui freine s'applique telle quelle ; une action sensible (dépense, licence, compte) vous sera reconfirmée dans le chat.</p>
<form method="post" action="/pilot/message" class="stacked"><label>Message<textarea name="body" required maxlength="${MAX}" rows="3" placeholder="Ex. : priorité aux bugs de la bulle cette semaine"></textarea></label><button type="submit">Envoyer à Claude</button></form></article></div>
<h3 class="sub">Questions de Claude</h3>${questionList ? `<ul class="reports">${questionList}</ul>` : `<p class="empty">Aucune question.</p>`}
<h3 class="sub">Messages envoyés</h3>${messageList ? `<ul class="reports">${messageList}</ul>` : `<p class="empty">Aucun message.</p>`}
<h3 class="sub">PR ouvertes ${snapshot ? `<small>relevé du ${escape(when(snapshot))}</small>` : ""}</h3>${prRows
    ? `<article class="card"><div class="scroll"><table><thead><tr><th>PR</th><th>Titre</th><th>CI</th><th>Fusion</th><th></th></tr></thead><tbody>${prRows}</tbody></table></div></article>`
    : `<p class="empty">Aucune PR ouverte au dernier relevé.</p>`}
<h3 class="sub">Journal de Claude <small>50 dernières actions</small></h3>${journalList ? `<ul class="reports">${journalList}</ul>` : `<p class="empty">Rien pour l'instant.</p>`}
</section>`;
}

/** Actions du formulaire ; renvoie null si le chemin n'est pas une action de pilotage. */
export async function pilotAction(path: string, form: FormData, db: D1Database, user: string, now: number): Promise<Response | null> {
  const text = (name: string) => String(form.get(name) ?? "").trim().slice(0, MAX);
  const run = (sql: string, ...args: unknown[]) => db.prepare(sql).bind(...args).run();
  if (path === "/pilot/pause") {
    await run("INSERT OR REPLACE INTO pilot_state (key, value, at, by) VALUES ('paused', ?, ?, ?)", text("paused") === "1" ? "1" : "0", now, user);
  } else if (path === "/pilot/message") {
    if (!text("body")) return new Response("Message vide", { status: 400 });
    await run("INSERT INTO pilot_messages (at, by, body) VALUES (?, ?, ?)", now, user, text("body"));
  } else if (path === "/pilot/answer") {
    if (!text("answer")) return new Response("Réponse vide", { status: 400 });
    await run("UPDATE pilot_questions SET answer = ?, answered_at = ?, answered_by = ? WHERE id = ? AND answer IS NULL", text("answer"), now, user, Number(text("id")));
  } else if (path === "/pilot/hold") {
    const repo = text("repo"), number = Number(text("number"));
    if (!/^[\w.-]+$/.test(repo) || !Number.isInteger(number) || number <= 0) return new Response("PR invalide", { status: 400 });
    if (text("hold") === "1") await run("INSERT OR REPLACE INTO pilot_holds (repo, number, reason, at, by) VALUES (?, ?, ?, ?, ?)", repo, number, text("reason").slice(0, 200) || null, now, user);
    else await run("DELETE FROM pilot_holds WHERE repo = ? AND number = ?", repo, number);
  } else return null;
  console.log(JSON.stringify({ event: "console.pilot", action: path, by: user }));
  return new Response(null, { status: 303, headers: { location: "/#claude" } });
}
