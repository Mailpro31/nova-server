/**
 * Des mots simples (lot 4, demande de Sash du 11/11).
 *
 * - La carte « État du moteur » commence par une phrase que tout le monde
 *   comprend ; les sous-moteurs disent à quoi ils servent.
 * - L'import des comptes dit ce qu'il fait avant de dire « SCIM ».
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fr } from "./messages.fr";

const RUNTIME = readFileSync("src/pages/AIRuntime.tsx", "utf8");

describe("carte État du moteur", () => {
  test("elle commence par une phrase simple", () => {
    expect(RUNTIME).toContain("engineSummary(");
    expect(RUNTIME).toContain("t.home.allGood");
  });
  test("les moteurs disent à quoi ils servent", () => {
    expect(fr.aiRuntime.textEngine).toBe("IA de texte (mise en forme, bulle)");
    expect(fr.aiRuntime.speechEngine).toBe("Dictée (transcription de la voix)");
  });
});

describe("import des comptes", () => {
  test("le titre et l'introduction parlent d'abord de l'annuaire", () => {
    expect(fr.provisioning.title).toBe("Import automatique des comptes");
    expect(fr.provisioning.lead.startsWith("Importer et tenir à jour")).toBe(true);
    expect(fr.provisioning.status).toBe("Import automatique (SCIM)");
  });
});
