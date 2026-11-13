import { bars, escape, num } from "../dashboard";
import { kpi, type Page } from "../shell";
import { fleetOrError } from "./ecoles";

const sum = (xs: (number | undefined)[]) => xs.reduce<number>((a, b) => a + (b ?? 0), 0);

export const usage: Page = {
  id: "usage",
  path: "/usage",
  async render(ctx) {
    const r = await fleetOrError(ctx, "Usage");
    if ("error" in r) return r.error;
    const days = ctx.url.searchParams.get("jours") === "7" ? 7 : 14;
    const schools = r.fleet.schools.map((s) => ({ ...s, dict: s.dictSeries.slice(-days), lat: s.latencySeries.slice(-days) }));
    const last = schools.map((s) => s.lat.at(-1)?.[1]).filter((v): v is number => v !== undefined).sort((a, b) => a - b);
    const median = last.length ? (last[(last.length - 1) >> 1] + last[last.length >> 1]) / 2 : undefined;
    const tab = (n: number) => `<a href="/usage?jours=${n}"${n === days ? ' class="on"' : ""}>${n} jours</a>`;
    const cards = schools.map((s) => `<article class="card"><header><h3><a href="/ecoles/${encodeURIComponent(s.id)}">${escape(s.id)}</a></h3></header>`
      + `<div class="charts"><figure><figcaption>Dictées par jour</figcaption>${bars(s.dict, "dictées")}</figure>`
      + `<figure><figcaption>Temps de transcription (95 % des dictées, en s)</figcaption>${bars(s.lat, "s")}</figure></div></article>`).join("");
    return {
      title: "Usage",
      body: `<div class="toolbar"><div class="seg">${tab(7)}${tab(14)}</div></div>
<div class="kpis">${kpi("Élèves actifs ce mois", num(sum(schools.map((s) => s.activeMonth))))}${kpi("Dictées sur 24 h", num(sum(schools.map((s) => s.dictations24h))))}`
        + `${kpi("Dictées sur la période", num(sum(schools.flatMap((s) => s.dict.map(([, v]) => v)))))}${kpi("Temps de transcription médian", `${num(median, 2)} <small>s</small>`)}</div>`
        + `<div class="stack">${cards || `<p class="empty">Aucune école.</p>`}</div>`,
    };
  },
};
