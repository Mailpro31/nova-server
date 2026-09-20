/**
 * La synchronisation Google Workspace, vue depuis la console.
 *
 * L'écran demande une adresse d'administrateur délégué et une *référence* de
 * secret — jamais la clé elle-même, qu'un champ de formulaire enverrait droit
 * en base. Il montre la dernière synchronisation, ce qu'elle a changé, et
 * permet de la relancer.
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

describe("Directory sync client", () => {
  test("the three routes are declared", () => {
    const client = code("src/lib/controlPlane.ts");
    expect(client).toContain("directorySync(organizationId: string)");
    expect(client).toContain(
      "setDirectorySync(organizationId: string, body: DirectorySyncBody)",
    );
    expect(client).toContain("runDirectorySync(organizationId: string)");
    expect(client).toContain("/directory-sync");
  });
});

describe("Directory sync page", () => {
  const page = code("src/pages/Provisioning.tsx");

  test("it is configured, saved and run from Provisioning", () => {
    expect(page).toContain("control!.setDirectorySync(");
    expect(page).toContain("control!.runDirectorySync(");
    expect(page).toContain("t.googleSync.title");
  });

  test("the key is a reference, and the page says so", async () => {
    const { t } = await import("./messages");
    expect(t.googleSync.keyHint.toLowerCase()).toContain("env://");
    expect(t.googleSync.keyHint.toLowerCase()).toContain("never");
    // Aucun champ ne demande la clé elle-même.
    expect(page).not.toContain("private_key");
    expect(page).toContain("t.googleSync.keyHint");
  });

  test("the guide says the access is read-only and delegated", async () => {
    const { t } = await import("./messages");
    const steps = t.googleSync.steps.join(" ").toLowerCase();
    expect(t.googleSync.steps.length).toBeGreaterThanOrEqual(4);
    expect(steps).toContain("read-only");
    expect(steps).toContain("domain-wide delegation");
    expect(steps).toContain("service account");
  });

  test("the last run is shown, with what it changed", async () => {
    const { t } = await import("./messages");
    expect(page).toContain("view.sync.last_status");
    expect(t.googleSync.outcome(3, 2, 1).toLowerCase()).toContain("3");
    expect(t.googleSync.hourly.toLowerCase()).toContain("hour");
  });

  test("server refusals are explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("invalid", "GOOGLE_KEY_REFERENCE_REQUIRED")).toBe(
      t.googleSync.keyReferenceRequired,
    );
    expect(errorMessage("invalid", "GOOGLE_ADMIN_SUBJECT_REQUIRED")).toBe(
      t.googleSync.adminSubjectRequired,
    );
    expect(errorMessage("server", "GOOGLE_UNAUTHORIZED")).toBe(
      t.googleSync.unauthorized,
    );
    expect(errorMessage("server", "GOOGLE_FORBIDDEN")).toBe(t.googleSync.forbidden);
    expect(errorMessage("conflict", "DIRECTORY_SYNC_DISABLED")).toBe(
      t.googleSync.disabled,
    );
  });
});
