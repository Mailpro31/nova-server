#!/usr/bin/env node
// Côté Claude du pilotage de la console (tables pilot_* de la D1 nova-activation).
//   node scripts/pilot.mjs inbox                              état, PR retenues, consignes non lues (marquées lues), réponses
//   node scripts/pilot.mjs log <kind> <ok|echec|info> <titre> [lien] < rapport.txt
//   node scripts/pilot.mjs ask <titre> < details.txt
//   node scripts/pilot.mjs reply <id> < reponse.txt
//   node scripts/pilot.mjs prs                                relève les PR ouvertes (nova-desktop, nova-server)
// Rapport de bug attendu : Symptôme / Journaux (extraits utiles) / Cause / Correctif (PR) / Preuve (tests, mesure).
// Jamais de texte d'élève, de jeton ni de clé dans un rapport : la console les affiche.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const here = new URL("..", import.meta.url);
// shell : npx est un .cmd sous Windows ; seules des requêtes fixes passent en ligne de commande, le texte libre passe par fichier.
const wrangler = (args) => execFileSync("npx", ["wrangler", "d1", "execute", "nova-activation", "--remote", "-c", "wrangler.console.toml", ...args],
  { cwd: here, encoding: "utf8", shell: process.platform === "win32", stdio: ["ignore", "pipe", "pipe"] });
const lit = (v) => (v === null || v === undefined ? "NULL" : typeof v === "number" ? String(v) : `'${String(v).replace(/'/g, "''")}'`);
const query = (sql) => JSON.parse(wrangler(["--json", "--command", JSON.stringify(sql)]))[0].results;
function write(sql) { // fichier : le texte long (journaux) ne passe pas par la ligne de commande
  const dir = mkdtempSync(join(tmpdir(), "pilot-"));
  try { writeFileSync(join(dir, "q.sql"), sql); wrangler(["--file", join(dir, "q.sql")]); } finally { rmSync(dir, { recursive: true, force: true }); }
}
const stdin = () => { try { return readFileSync(0, "utf8").trim(); } catch { return ""; } };
const now = () => Math.floor(Date.now() / 1000);
const [cmd, ...args] = process.argv.slice(2);

if (cmd === "inbox") {
  const paused = query("SELECT value, by, at FROM pilot_state WHERE key = 'paused'")[0];
  const holds = query("SELECT repo, number, reason FROM pilot_holds ORDER BY repo, number");
  const messages = query("SELECT id, at, by, body FROM pilot_messages WHERE read_at IS NULL ORDER BY id");
  const answers = query("SELECT id, title, answer FROM pilot_questions WHERE answer IS NOT NULL AND answered_at > " + (now() - 7 * 86400));
  const open = query("SELECT id, title FROM pilot_questions WHERE answer IS NULL");
  if (messages.length) write(`UPDATE pilot_messages SET read_at = ${now()} WHERE id IN (${messages.map((m) => m.id).join(",")});`);
  console.log(JSON.stringify({ paused: paused?.value === "1", paused_by: paused?.by ?? null, holds, messages, answers_last_7_days: answers, open_questions: open }, null, 2));
} else if (cmd === "log") {
  const [kind, result, title, link] = args;
  if (!kind || !["ok", "echec", "info"].includes(result) || !title) throw new Error("usage : log <kind> <ok|echec|info> <titre> [lien] < rapport");
  write(`INSERT INTO pilot_journal (at, kind, result, title, link, details) VALUES (${now()}, ${lit(kind)}, ${lit(result)}, ${lit(title)}, ${lit(link)}, ${lit(stdin() || null)});`);
} else if (cmd === "ask") {
  if (!args[0]) throw new Error("usage : ask <titre> < details");
  write(`INSERT INTO pilot_questions (at, title, details) VALUES (${now()}, ${lit(args[0])}, ${lit(stdin() || null)});`);
} else if (cmd === "reply") {
  const id = Number(args[0]);
  if (!Number.isInteger(id)) throw new Error("usage : reply <id> < reponse");
  write(`UPDATE pilot_messages SET reply = ${lit(stdin())}, replied_at = ${now()}, read_at = COALESCE(read_at, ${now()}) WHERE id = ${id};`);
} else if (cmd === "prs") {
  const t = now(), rows = [];
  for (const repo of ["nova-desktop", "nova-server"]) {
    const prs = JSON.parse(execFileSync("gh", ["pr", "list", "-R", `Mailpro31/${repo}`, "--json", "number,title,url,statusCheckRollup"], { encoding: "utf8" }));
    for (const p of prs) {
      const states = (p.statusCheckRollup ?? []).map((c) => c.conclusion || c.status || c.state || "");
      const ci = !states.length ? "aucune" : states.some((s) => /FAIL|ERROR|CANCEL|TIMED_OUT/i.test(s)) ? "rouge"
        : states.every((s) => /SUCCESS|SKIPPED|NEUTRAL/i.test(s)) ? "vert" : "en cours";
      rows.push(`(${lit(repo)}, ${p.number}, ${lit(p.title)}, ${lit(ci)}, ${lit(p.url)}, ${t})`);
    }
  }
  write(`DELETE FROM pilot_prs;${rows.length ? ` INSERT INTO pilot_prs (repo, number, title, ci, url, seen_at) VALUES ${rows.join(", ")};` : ""}`);
  console.log(`${rows.length} PR relevées`);
} else {
  console.error("commandes : inbox | log | ask | reply | prs");
  process.exit(2);
}
