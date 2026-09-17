/**
 * Synchronisation automatique depuis l'annuaire, expliquée dans la console.
 *
 * Microsoft Entra et tout annuaire compatible SCIM (Okta, JumpCloud, OneLogin,
 * Keycloak…) créent comptes et groupes tout seuls. La page Provisioning doit
 * dire comment l'activer pour chacun, et ce qui arrive au départ d'une
 * personne.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

describe("Provisioning guides", () => {
  test("Microsoft Entra is set up for groups, not for a single test user", async () => {
    const { t } = await import("./messages");
    const steps = t.provisioning.entraSteps.join(" ").toLowerCase();
    expect(steps).toContain("groups");
    expect(steps).not.toContain("one controlled test user");
  });

  test("any other SCIM directory has its own guide", async () => {
    const { t } = await import("./messages");
    expect(t.provisioning.genericTitle).toContain("SCIM");
    expect(t.provisioning.genericLead).toContain("Okta");
    expect(t.provisioning.genericSteps.length).toBeGreaterThanOrEqual(3);
    expect(code("src/pages/Provisioning.tsx")).toContain("t.provisioning.genericSteps.map");
  });

  test("the page says what happens when someone leaves", async () => {
    const { t } = await import("./messages");
    const note = t.provisioning.departureNote.toLowerCase();
    expect(note).toContain("suspended");
    expect(note).toContain("signed out");
    expect(code("src/pages/Provisioning.tsx")).toContain("t.provisioning.departureNote");
  });
});
