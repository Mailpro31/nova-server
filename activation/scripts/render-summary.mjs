// Résume la sortie de render-audit.mjs : défauts groupés par catégorie et message normalisé, avec les pages concernées.
// Usage : node scripts/render-summary.mjs sortie.json [catégorie]
import { readFileSync } from "node:fs";

const [file, only] = process.argv.slice(2);
const data = JSON.parse(readFileSync(file, "utf8"));
const groups = new Map();
for (const [key, o] of Object.entries(data)) {
  const [page, vp, theme] = key.split("|");
  for (const [cat, list] of Object.entries(o)) {
    if (only && cat !== only) continue;
    if (cat === "bars") continue;
    for (const m of list) {
      if (cat === "clipped" && m.includes("aside.side")) continue; // la barre d'onglets mobile défile exprès
      const norm = m.replace(/«[^»]*»/g, "«…»").replace(/-?\d+(\.\d+)?/g, "N").slice(0, 160);
      const g = groups.get(`${cat} | ${norm}`) ?? { n: 0, pages: new Set(), ex: m };
      g.n++; g.pages.add(`${page.replace(".html", "")}/${vp}/${theme}`); groups.set(`${cat} | ${norm}`, g);
    }
  }
}
for (const [k, g] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
  const pages = [...g.pages];
  console.log(`${k}\n   ×${g.n}, ${pages.length} pages ex: ${g.ex.slice(0, 200)}\n   ${pages.slice(0, 4).join(", ")}`);
}
