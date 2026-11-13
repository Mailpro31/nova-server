import { afterEach, describe, expect, it, vi } from "vitest";
import { bars, loadFleet, type MetricsEnv } from "../src/dashboard";

const NOW = 1_800_000_000;
const env: MetricsEnv = {
  PROM_URL: "https://prom.test/api/prom", PROM_USER: "1", LOKI_URL: "https://loki.test", LOKI_USER: "2", GRAFANA_READ_TOKEN: "t",
};
const vector = (rows: [Record<string, string>, number][]) =>
  ({ data: { result: rows.map(([metric, v]) => ({ metric, value: [NOW, String(v)] })) } });

// Réponse de Grafana Cloud selon la requête PromQL ou LogQL.
function grafana(loki: unknown | null) {
  return vi.fn(async (url: string) => {
    const u = new URL(url);
    if (u.host === "loki.test") return loki === null ? new Response("", { status: 401 }) : new Response(JSON.stringify({ data: loki }));
    const q = u.searchParams.get("query") ?? "";
    const s = { school: "ipsa" };
    let body: unknown = vector([]);
    if (q.includes("timestamp(nova_uptime_seconds)")) body = vector([[s, NOW - 30]]);
    else if (q.includes("nova_server_info")) body = vector([[{ ...s, server_version: "0.2.0" }, 1]]);
    else if (q.includes("nova_app_versions")) body = vector([[{ ...s, version: "1.0.82" }, 3]]);
    else if (q.includes("nova_licence_info")) body = vector([[{ ...s, status: "valid", tier: "151-250" }, 1]]);
    else if (q.includes("nova_licence_days_left")) body = vector([[s, 120]]);
    else if (q.includes("nova_ai_spend_eur_month")) body = vector([[s, 50]]);
    else if (q.includes("nova_ai_envelope_eur")) body = vector([[s, 1200]]);
    else if (u.pathname.endsWith("query_range")) body = { data: { result: [{ metric: s, values: [[NOW - 86400, "4"], [NOW, "7"]] }] } };
    return new Response(JSON.stringify(body));
  });
}
afterEach(() => vi.unstubAllGlobals());

describe("données de la flotte", () => {
  it("rassemble état, licence, versions et dépense par école", async () => {
    vi.stubGlobal("fetch", grafana({ result: [] }));
    const r = await loadFleet(env, NOW);
    if (!r.ok) throw new Error(r.reason);
    const [ipsa] = r.fleet.schools;
    expect(ipsa).toMatchObject({ id: "ipsa", online: true, serverVersion: "0.2.0", spendMonth: 50, envelopeAnnual: 1200,
      licence: { status: "valid", tier: "151-250", daysLeft: 120 }, apps: [{ version: "1.0.82", count: 3 }] });
    expect(ipsa.dictSeries).toEqual([[NOW - 86400 - 43200, 4], [NOW - 43200, 7]]); // points à minuit → étiquetés à midi du jour couvert
    expect(r.fleet.reports).toEqual([]);
  });
  it("marque hors ligne un serveur muet depuis plus de 10 minutes", async () => {
    const f = grafana({ result: [] });
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      decodeURIComponent(url).includes("timestamp(nova_uptime_seconds)")
        ? new Response(JSON.stringify(vector([[{ school: "ipsa" }, NOW - 7200]]))) : f(url)));
    const r = await loadFleet(env, NOW);
    expect(r.ok && r.fleet.schools[0].online).toBe(false);
  });
  it("ne compte comme dictées que les appels servis, pas les refus de quota", async () => {
    const f = grafana({ result: [] });
    const asked: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => { asked.push(decodeURIComponent(url)); return f(url); }));
    await loadFleet(env, NOW);
    const dict = asked.filter((q) => q.includes("inference_transcribe"));
    expect(dict).toHaveLength(3); // 24 h, 13 jours finis, aujourd'hui
    for (const q of dict) expect(q).toContain('outcome="allowed"');
  });
  it("garde une école muette dans la liste : la requête remonte plus loin que les 5 min de Prometheus", async () => {
    const f = grafana({ result: [] });
    let seen = "";
    vi.stubGlobal("fetch", vi.fn(async (url: string) => { const q = decodeURIComponent(url); if (q.includes("timestamp(nova_uptime_seconds)")) seen = q; return f(url); }));
    await loadFleet(env, NOW);
    expect(seen).toMatch(/max_over_time\(timestamp\(nova_uptime_seconds\)\[\d+d:\d+m\]\)/);
  });
  it("journaux illisibles → reports null, le reste arrive", async () => {
    vi.stubGlobal("fetch", grafana(null));
    const r = await loadFleet(env, NOW);
    expect(r.ok && r.fleet.reports).toBeNull();
  });
  it("lit les signalements une seule fois, même arrivés par deux flux", async () => {
    const line = { event: "support.report", app_version: "1.0.81", engines: { transcription_model: "warning" }, description: "<b>micro</b>" };
    vi.stubGlobal("fetch", grafana({ result: [1, 2].map((n) => ({ stream: { school: "ipsa", service_name: `s${n}` }, values: [[String(NOW * 1e9), JSON.stringify(line)]] })) }));
    const r = await loadFleet(env, NOW);
    expect(r.ok && r.fleet.reports).toHaveLength(1);
  });
  it("prévient sans jeton ni réponse", async () => {
    expect(await loadFleet({ ...env, GRAFANA_READ_TOKEN: "" }, NOW)).toEqual({ ok: false, reason: "token" });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    expect(await loadFleet(env, NOW)).toEqual({ ok: false, reason: "grafana" });
  });
});

describe("barres", () => {
  it("une barre par jour, sans mesure → message", () => {
    expect(bars([], "x")).toContain("Pas encore de mesure");
    const html = bars([[NOW, 1], [NOW + 86400, 1834]], "dictées");
    expect((html.match(/class="col[ "]/g) ?? []).length).toBe(2);
    expect(html.replace(/\s/g, " ")).toContain("1 834 dictées"); // valeur exacte au survol
    expect(bars([[NOW, 1.234]], "s")).toContain("1,23 s");
  });
});

describe("bars : valeurs limites", () => {
  it("axe à 0 quand tout est nul, seuil lisible pour une valeur minuscule, une seule date pour un point", () => {
    expect(bars([[NOW, 0], [NOW + 86400, 0]], "dictées")).toContain("max 0 dictées");
    const tiny = bars([[NOW, 0.004]], "s");
    expect(tiny).toContain("max &#60; 0,01 s");
    expect(tiny).toContain('style="--n:1"');
    expect(tiny.match(/<span>/g)).toHaveLength(1);
  });
});
