/**
 * Le menu simplifié (lot 2, demande de Sash du 11/11).
 *
 * - Le suivi (usage, adoption, abonnement, limites) a son domaine : rangé sous
 *   « Limites », on ne l'y cherchait pas.
 * - Les pages techniques (identité, sécurité, déploiement, diagnostic) passent
 *   sous « Avancé », replié par défaut : une direction n'y va presque jamais.
 * - Des noms de pages en français courant.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { NAV_SECTIONS } from "./navigation";
import { fr } from "./messages.fr";

const APP = readFileSync("src/App.tsx", "utf8");

describe("menu simplifié", () => {
  test("les domaines, dans l'ordre d'usage", () => {
    expect(NAV_SECTIONS.map((s) => s.id)).toEqual([
      "overview", "people", "features", "content", "tracking", "audit", "advanced",
    ]);
  });

  test("le suivi regroupe usage, adoption, abonnement et limites", () => {
    expect(NAV_SECTIONS.find((s) => s.id === "tracking")?.pages).toEqual([
      "usage", "adoption", "subscription", "limits",
    ]);
  });

  test("les pages techniques sont sous Avancé, replié par défaut", () => {
    const advanced = NAV_SECTIONS.find((s) => s.id === "advanced");
    expect(advanced?.pages).toEqual(["identity", "security", "deployment", "diagnostics"]);
    expect(advanced?.collapsed).toBe(true);
    expect(APP).toContain("<details");
  });

  test("des noms en français courant", () => {
    expect(fr.nav.learn).toBe("Apprendre");
    expect(fr.nav.packages).toBe("Contenus");
    expect(fr.nav.provisioning).toBe("Import des comptes");
    expect(fr.navGroups.tracking).toBe("Suivi");
    expect(fr.navGroups.advanced).toBe("Avancé");
  });
});
