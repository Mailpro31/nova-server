// Ouvre chaque HTML de RENDER_DIR (voir test/render-pages.test.ts) dans agent-browser, 2 largeurs × 2 thèmes, et mesure la page.
// Usage : node scripts/render-audit.mjs <dossier-html> <sortie.json> [filtre-sous-chaine]
import { spawnSync } from "node:child_process";
import { readdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const [dir, outFile, only] = process.argv.slice(2);
const init = resolve(import.meta.dirname, "render-audit.js");
const files = readdirSync(dir, { recursive: true }).map((f) => f.replaceAll("\\", "/")).filter((f) => f.endsWith(".html") && (!only || f.includes(only)));
const ab = (args, input) => spawnSync("agent-browser", args, { input, encoding: "utf8", shell: true, maxBuffer: 1 << 28 });
ab(["close", "--all"]);
// Le démon hérite des tubes : on le lance avec stdio ignoré, sinon l'appel ne rend jamais la main.
spawnSync("agent-browser", ["--allow-file-access", "--init-script", init, "open", "about:blank"], { stdio: "ignore", shell: true });
const results = {};
for (const [w, h, vp] of [[393, 852, "mobile"], [1280, 800, "bureau"]]) for (const theme of ["clair", "sombre"]) {
  const cmds = [["set", "viewport", String(w), String(h)], ["set", "media", theme === "sombre" ? "dark" : "light"]];
  for (const f of files) cmds.push(["open", `file:///${resolve(dir, f).replaceAll("\\", "/")}`], ["eval", "'AUDIT' + JSON.stringify(window.__audit())"]);
  const r = ab(["batch"], JSON.stringify(cmds));
  const lines = r.stdout.split(/\r?\n/).filter((l) => l.startsWith('"AUDIT'));
  if (lines.length !== files.length) console.error(`${vp}/${theme}: ${lines.length} mesures pour ${files.length} pages`, r.stderr.slice(0, 300), r.stdout.slice(0, 300));
  files.forEach((f, i) => { if (lines[i]) results[`${f}|${vp}|${theme}`] = JSON.parse(JSON.parse(lines[i]).slice(5)); });
}
writeFileSync(outFile, JSON.stringify(results, null, 1));
const bad = Object.entries(results).filter(([, o]) => ["hscroll", "overflow", "clipped", "tips", "contrast", "touch", "focus", "misc"].some((k) => o[k].length));
console.log(`${Object.keys(results).length} mesures, ${bad.length} avec défaut`);
