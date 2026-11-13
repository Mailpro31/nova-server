import { afterEach, describe, expect, it, vi } from "vitest";
import { bars, dashboard, type MetricsEnv } from "../src/dashboard";

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

describe("tableau de bord", () => {
  it("montre l'état, la licence et la part d'IA consommée", async () => {
    vi.stubGlobal("fetch", grafana({ result: [] }));
    const html = await dashboard(env, NOW);
    expect(html).toContain("En ligne");
    expect(html).toContain("1.0.82 ×3");
    expect(html).toContain("Valide · 151-250 · 120 j");
    expect(html).toContain("50 %"); // 50 € sur 1 200 / 12
    expect(html).toContain("Aucun signalement depuis 30 jours.");
  });
  it("signale un serveur muet", async () => {
    const f = grafana({ result: [] });
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      decodeURIComponent(url).includes("timestamp(nova_uptime_seconds)")
        ? new Response(JSON.stringify(vector([[{ school: "ipsa" }, NOW - 7200]]))) : f(url)));
    expect(await dashboard(env, NOW)).toContain("Muet depuis 2 h");
  });
  it("dit quoi faire si les journaux sont illisibles", async () => {
    vi.stubGlobal("fetch", grafana(null));
    expect(await dashboard(env, NOW)).toContain("logs:read");
  });
  it("affiche les signalements, échappés", async () => {
    const line = { event: "support.report", app_version: "1.0.81", engines: { microphone: "ok", transcription_model: "warning" }, last_error: "", description: "<b>micro</b>" };
    vi.stubGlobal("fetch", grafana({ result: [{ stream: { school: "ipsa" }, values: [[String(NOW * 1e9), JSON.stringify(line)]] }] }));
    const html = await dashboard(env, NOW);
    expect(html).toContain("transcription_model : warning");
    expect(html).toContain("&#60;b&#62;micro");
    expect(html).not.toContain("<b>micro");
  });
  it("prévient sans jeton ni réponse", async () => {
    expect(await dashboard({ ...env, GRAFANA_READ_TOKEN: "" }, NOW)).toContain("GRAFANA_READ_TOKEN");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    expect(await dashboard(env, NOW)).toContain("ne répond pas");
  });
});

describe("barres", () => {
  it("une barre par jour, sans mesure → message", () => {
    expect(bars([], "x")).toContain("Pas encore de mesure");
    expect((bars([[NOW, 1], [NOW + 86400, 2]], "x").match(/<rect/g) ?? []).length).toBe(2);
  });
});
