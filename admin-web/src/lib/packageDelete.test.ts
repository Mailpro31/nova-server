/**
 * Supprimer un package archivé.
 *
 * La console ne propose la suppression qu'à un package déjà archivé, derrière
 * une confirmation « danger » qui dit que rien ne se récupère.
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

describe("Package deletion", () => {
  test("the client deletes a package by id", () => {
    const client = code("src/lib/controlPlane.ts");
    expect(client).toContain(
      "deletePackage(organizationId: string, packageId: string)",
    );
    expect(client).toContain('method: "DELETE"');
  });

  test("only an archived package offers deletion, behind a danger confirmation", async () => {
    const { t } = await import("./messages");
    const page = code("src/pages/Packages.tsx");
    expect(page).toContain('status === "archived"');
    expect(page).toContain("t.packages.deleteTitle");
    expect(page).toContain("control!.deletePackage(");
    expect(page).toContain('tone="danger"');
    expect(t.packages.deleteBody.toLowerCase()).toContain("cannot be undone");
  });

  test("the server refusal is explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("conflict", "PACKAGE_NOT_ARCHIVED")).toBe(
      t.packages.notArchived,
    );
  });
});
