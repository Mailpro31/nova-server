/**
 * La page d'accueil de la console (lot 3, demande de Sash du 11/11).
 *
 * Elle répond d'abord à « est-ce que tout marche ? », puis donne les chiffres
 * que le serveur fournit déjà (jamais un chiffre inventé), les alertes qui
 * demandent une action et des raccourcis vers les tâches courantes. Les
 * réglages techniques restent accessibles, repliés.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { engineSummary, HOME_SHORTCUTS } from "./home";
import { fr } from "./messages.fr";

const APP = readFileSync("src/App.tsx", "utf8");
const OVERVIEW = readFileSync("src/pages/Overview.tsx", "utf8");

describe("état en une ligne", () => {
  test("sain : tout fonctionne", () => {
    expect(engineSummary("healthy")).toBe("ok");
  });
  test("dégradé ou en démarrage : à surveiller", () => {
    expect(engineSummary("degraded")).toBe("warn");
    expect(engineSummary("starting")).toBe("warn");
  });
  test("indisponible : en panne", () => {
    expect(engineSummary("unavailable")).toBe("down");
  });
});

describe("raccourcis", () => {
  test("quatre tâches courantes, vers des pages qui existent", () => {
    expect(HOME_SHORTCUTS.map((s) => s.path)).toEqual(["members", "usage", "groups", "audit"]);
    for (const { path } of HOME_SHORTCUTS) expect(APP).toContain(`path: "${path}"`);
  });
});

describe("page d'accueil", () => {
  test("elle charge l'abonnement, l'adoption, l'usage et le moteur", () => {
    for (const call of ["control.subscription()", "control.adoption()", "control.usageMetrics(24)", "control.aiRuntime()"])
      expect(OVERVIEW).toContain(call);
  });
  test("les réglages techniques restent là, repliés", () => {
    expect(OVERVIEW).toContain("<details");
    expect(OVERVIEW).toContain("t.home.technical");
  });
  test("des textes en français", () => {
    expect(fr.home.allGood).toBe("Tout fonctionne");
    expect(fr.home.activeMonth).toBe("Personnes actives ce mois-ci");
  });
});
