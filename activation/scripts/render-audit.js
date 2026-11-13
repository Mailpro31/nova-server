// Script d'initialisation d'agent-browser : définit window.__audit() qui mesure la page affichée.
// Utilisé par scripts/render-audit.mjs ; ne fait rien tout seul.
window.__audit = () => {
  // Mesure sans barre de défilement : un téléphone a des barres flottantes qui ne prennent pas de place.
  const nb = document.createElement("style"); nb.textContent = "html{scrollbar-width:none}"; document.head.append(nb);
  const out = { hscroll: [], overflow: [], clipped: [], tips: [], contrast: [], touch: [], focus: [], bars: [], misc: [] };
  const D = document.documentElement, W = D.clientWidth;
  const short = (el) => `${el.tagName.toLowerCase()}${el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : ""}«${(el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 28)}»`;
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  if (D.scrollWidth > W + 1) out.hscroll.push(`document ${D.scrollWidth} > ${W}`);
  if (document.body.scrollWidth > W + 1) out.hscroll.push(`body ${document.body.scrollWidth} > ${W}`);
  const scrollers = (el) => { for (let p = el.parentElement; p; p = p.parentElement) { const o = getComputedStyle(p); if (/(auto|scroll|hidden|clip)/.test(o.overflowX) && p !== document.body && p !== D) return p; } return null; };
  const all = [...document.querySelectorAll("body *")].filter(vis);

  // Débordements : hors de l'écran, hors de la carte, contenu coupé.
  for (const el of all) {
    if (el.closest("svg") && el.tagName !== "svg") continue;
    const r = el.getBoundingClientRect(), sc = scrollers(el);
    if (!sc && (r.right > W + 1 || r.left < -1)) out.overflow.push(`hors écran ${short(el)} ${Math.round(r.left)}..${Math.round(r.right)} / ${W}`);
    const box = el.parentElement && el.parentElement.closest(".card,.kpi,.stat,.empty,.notice,.seg,.row-link");
    if (box && box !== el && !sc) { const b = box.getBoundingClientRect(); if (r.right > b.right + 1 || r.left < b.left - 1) out.overflow.push(`hors carte ${short(el)} ${Math.round(r.left)}..${Math.round(r.right)} carte ${Math.round(b.left)}..${Math.round(b.right)}`); }
    const s = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0 && !el.matches("html,body,.cols,.col,button.col,td,th,table,input,textarea,select")) {
      if (/(auto|scroll)/.test(s.overflowX)) out.clipped.push(`défile ${short(el)} ${el.scrollWidth}>${el.clientWidth}`);
      else if (/(hidden|clip)/.test(s.overflowX) || s.textOverflow === "ellipsis") out.clipped.push(`coupé ${short(el)} ${el.scrollWidth}>${el.clientWidth}`);
    }
  }

  // Infobulles : on les force visibles et on vérifie qu'elles tiennent dans l'écran et dans leurs conteneurs.
  const st = document.createElement("style"); st.textContent = ".tip{display:block!important}"; document.head.append(st);
  for (const tip of document.querySelectorAll(".tip")) {
    const r = tip.getBoundingClientRect();
    if (r.left < -0.5 || r.right > W + 0.5) out.tips.push(`écran ${short(tip.parentElement)} ${Math.round(r.left)}..${Math.round(r.right)} / ${W}`);
    for (let p = tip.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p);
      if (p !== D && p !== document.body && (o.overflowX !== "visible" || o.overflowY !== "visible")) {
        const b = p.getBoundingClientRect();
        if (r.left < b.left - 0.5 || r.right > b.right + 0.5 || r.top < b.top - 0.5) out.tips.push(`masquée par ${short(p)} : ${Math.round(r.left)}..${Math.round(r.right)} vs ${Math.round(b.left)}..${Math.round(b.right)}, haut ${Math.round(r.top)} vs ${Math.round(b.top)}`);
      }
    }
  }
  st.remove();

  // Contraste WCAG AA.
  const parse = (c) => { const m = c.match(/[\d.]+/g); if (!m) return null; const n = m.map(Number); return { r: n[0], g: n[1], b: n[2], a: n.length > 3 ? n[3] : 1 }; };
  const over = (f, b) => ({ r: f.r * f.a + b.r * (1 - f.a), g: f.g * f.a + b.g * (1 - f.a), b: f.b * f.a + b.b * (1 - f.a), a: 1 });
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const bgOf = (el) => { const chain = []; for (let p = el; p; p = p.parentElement) { const c = parse(getComputedStyle(p).backgroundColor); if (c && c.a > 0) { chain.push(c); if (c.a === 1) break; } } let base = parse(getComputedStyle(document.body).backgroundColor); if (!base || base.a === 0) base = { r: 255, g: 255, b: 255, a: 1 }; if (chain.length && chain[chain.length - 1].a === 1) base = chain.pop(); for (const c of chain.reverse()) base = over(c, base); return base; };
  const ratio = (f, b) => { const x = lum(f), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const seen = new Set();
  for (const el of all) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own || /^(STYLE|SCRIPT)$/.test(el.tagName)) continue;
    const s = getComputedStyle(el), bg = bgOf(el), fgRaw = parse(s.color); if (!fgRaw) continue;
    const fg = over(fgRaw, bg), big = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && +s.fontWeight >= 700);
    const need = big ? 3 : 4.5, ra = ratio(fg, bg);
    if (ra < need) { const k = `${el.className}|${s.color}|${Math.round(ra * 10)}`; if (!seen.has(k)) { seen.add(k); out.contrast.push(`${ra.toFixed(2)}:1 < ${need} ${short(el)} fg ${s.color} bg rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`); } }
  }
  for (const el of document.querySelectorAll("input[placeholder]")) if (vis(el)) {
    const pc = parse(getComputedStyle(el, "::placeholder").color), bg = bgOf(el), ra = ratio(over(pc, bg), bg);
    if (ra < 4.5) out.contrast.push(`${ra.toFixed(2)}:1 placeholder ${short(el)}`);
  }

  // Cibles tactiles et focus clavier.
  const targets = [...document.querySelectorAll("a[href],button,input:not([type=hidden]),select,textarea,summary")].filter(vis);
  const mobile = W <= 760;
  for (const el of targets) {
    const r = el.getBoundingClientRect(), inline = el.tagName === "A" && getComputedStyle(el).display === "inline" && el.closest("p,li,blockquote,dd,.muted,.sub,.lead,summary,h2,h3,time");
    if (mobile && !inline && (r.width < 24 || r.height < 24)) out.touch.push(`${Math.round(r.width)}×${Math.round(r.height)} ${short(el)}`);
  }
  const seenF = new Set();
  for (const el of targets) {
    const key = el.tagName + el.className; if (seenF.has(key)) continue; seenF.add(key);
    el.focus({ focusVisible: true });
    const s = getComputedStyle(el), has = (s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0) || s.boxShadow !== "none";
    if (!has) out.focus.push(`pas de focus visible ${short(el)}`);
    else if (s.outlineStyle !== "none") { const oc = parse(s.outlineColor), bg = bgOf(el.parentElement || el), ra = ratio(over(oc, bg), bg); if (ra < 3) out.focus.push(`focus faible ${ra.toFixed(2)}:1 ${short(el)}`); }
    el.blur();
  }

  // Graphiques en barres : hauteurs et axe.
  const num = (t) => { const m = t.replace(/[\s  ]/g, "").match(/-?[\d,]+/); return m ? parseFloat(m[0].replace(",", ".")) : NaN; };
  for (const g of document.querySelectorAll(".bars")) {
    const cols = [...g.querySelectorAll(".col")], axis = num(g.querySelector(".axis").textContent);
    const vals = cols.map((c) => num(c.getAttribute("aria-label").split(" : ").pop()));
    const hs = cols.map((c) => { const i = c.querySelector("i"); return i.getBoundingClientRect().height / c.getBoundingClientRect().height * 100; });
    if (cols.some((c) => c.getAttribute("aria-label").includes("< "))) { out.bars.push(`${cols.length} barres, valeurs « < seuil » : non vérifiées`); continue; }
    const max = Math.max(...vals);
    const widths = cols.map((c) => c.getBoundingClientRect().width);
    out.bars.push(`${cols.length} barres, largeur ${Math.min(...widths).toFixed(0)}–${Math.max(...widths).toFixed(0)}px, axe max ${axis}, max valeur ${max}, hauteurs% ${hs.map((h) => h.toFixed(0)).join(",")}`);
    if (axis !== max) out.misc.push(`axe max ${axis} ≠ plus grande valeur ${max}`);
    cols.forEach((c, k) => {
      const v = vals[k];
      if (v === 0 && hs[k] > 0.5) out.misc.push(`barre ${k} = 0 mais hauteur ${hs[k].toFixed(1)}%`);
      if (v === max && max > 0 && Math.abs(hs[k] - 100) > 1) out.misc.push(`barre max ${k} hauteur ${hs[k].toFixed(1)}% ≠ 100`);
      if (max > 0 && v > 0 && Math.abs(hs[k] - (v / max) * 100) > 3 && hs[k] > 2.5) out.misc.push(`barre ${k} hauteur ${hs[k].toFixed(1)}% pour ${v}/${max}`);
    });
    const max_w = Math.max(...widths); if (max_w > 60) out.misc.push(`barres géantes ${max_w.toFixed(0)}px`);
  }
  if (/undefined|\bNaN\b|\[object/.test(document.body.innerText)) out.misc.push("texte « undefined/NaN/[object] » affiché");
  return out;
};
