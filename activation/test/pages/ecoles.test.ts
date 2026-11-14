import { afterEach, describe, expect, it, vi } from "vitest";
import { demoGrafana, get, harness, vector } from "../helpers";
import { projection } from "../../src/pages/ia";

afterEach(() => vi.unstubAllGlobals());

const html = async (r: Response) => r.text();
const at = (iso: string) => Date.parse(iso) / 1000;

describe("Écoles", () => {
  it("liste les écoles, avec l'état muet", async () => {
    const r = await get(await harness(demoGrafana), "/ecoles");
    const t = await html(r);
    expect(r.status).toBe(200);
    expect(t).toContain("ipsa");
    expect(t).toContain("lycee-test");
    expect(t).toContain("Muet depuis 2 h");
  });

  it("filtre les écoles muettes", async () => {
    const t = await html(await get(await harness(demoGrafana), "/ecoles?vue=muettes"));
    expect(t).toContain("lycee-test");
    expect(t).not.toContain('href="/ecoles/ipsa"');
  });

  it("rend la fiche d'une école, avec ses codes", async () => {
    const h = await harness(demoGrafana);
    await h.env.DB.prepare("INSERT INTO codes (code_hash, licence, school, expires_at) VALUES ('h', 'x.e30.y', 'IPSA ', ?)").bind(Math.floor(Date.now() / 1000) + 3600).run();
    const t = await html(await get(h, "/ecoles/ipsa"));
    for (const s of ["Valide", "1.0.82 ×31", "à jour", "Dictées par jour", "https://grafana.test/explore", "Le micro coupe", "/signalements?ecole=ipsa", "En attente", "Pas d'enveloppe dans la licence"]) {
      if (s === "Pas d'enveloppe dans la licence") expect(t).not.toContain(s);
      else expect(t).toContain(s);
    }
    expect(t).not.toContain("Aucun code donné à ce nom.");
    expect(await html(await get(await harness(demoGrafana), "/ecoles/ipsa"))).toContain("Aucun code donné à ce nom.");
  });

  it("une version de poste « other » s'affiche comme inconnue, pas comme ancienne", async () => {
    const h = await harness((url) => {
      const base = demoGrafana(url);
      if ((url.searchParams.get("query") ?? "").includes("nova_app_versions"))
        return vector([[{ school: "ipsa", version: "1.0.82" }, 3], [{ school: "ipsa", version: "other" }, 2]]);
      return base;
    });
    const t = await html(await get(h, "/ecoles/ipsa"));
    expect(t).toContain("other ×2</span> <span");
    expect(t).toContain("inconnue");
    expect(t.match(/ancienne/g) ?? []).toHaveLength(0);
  });

  it("signale une école inconnue", async () => {
    const t = await html(await get(await harness(demoGrafana), "/ecoles/inconnue"));
    expect(t).toContain("École inconnue");
    expect(t).toContain('href="/ecoles"');
  });

  it("échappe et encode l'identifiant d'école", async () => {
    const evil = "<script>alert(1)</script>";
    const grafana = (url: URL) => {
      const q = url.searchParams.get("query") ?? "";
      if (url.host === "loki.test") return { data: { result: [] } };
      if (url.pathname.endsWith("query_range")) return { data: { result: [] } };
      if (q.includes("timestamp(")) return vector([[{ school: evil }, Math.floor(Date.now() / 1000)]]);
      return vector([]);
    };
    const h = await harness(grafana);
    for (const path of ["/ecoles", `/ecoles/${encodeURIComponent(evil)}`, "/usage", "/ia"]) {
      const t = await html(await get(h, path));
      expect(t).not.toContain("<script>alert");
      expect(t).toContain("&#60;script&#62;");
    }
    expect(await html(await get(h, "/ecoles"))).toContain(`/ecoles/${encodeURIComponent(evil)}`);
  });

  it("rend Usage et Coûts d'IA", async () => {
    const h = await harness(demoGrafana);
    const u = await get(h, "/usage?jours=7");
    expect(u.status).toBe(200);
    expect(await html(u)).toContain("Dictées par jour");
    const i = await get(h, "/ia");
    expect(i.status).toBe(200);
    expect(await html(i)).toContain("Projection");
  });

  it("calcule la projection en heure de Paris", () => {
    expect(projection(15, at("2026-06-15T12:00:00Z"))).toBe(30);
    expect(projection(2, at("2026-07-01T12:00:00Z"))).toBe(62);
    expect(projection(10, at("2026-07-16T12:00:00Z"))).toBeCloseTo(19.375);
    // 30 juin 23 h UTC = 1er juillet 1 h à Paris
    expect(projection(1, at("2026-06-30T23:00:00Z"))).toBe(31);
  });

  it("annonce Grafana injoignable", async () => {
    const h = await harness();
    for (const path of ["/ecoles", "/usage", "/ia"]) {
      const r = await get(h, path);
      expect(r.status).toBe(200);
      expect(await html(r)).toContain("Grafana Cloud ne répond pas");
    }
  });
});
