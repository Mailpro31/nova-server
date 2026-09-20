/**
 * L'import d'une liste de membres, vu depuis la console.
 *
 * On voit avant d'écrire : le fichier est d'abord envoyé en aperçu, et rien
 * n'est enregistré tant que l'administrateur n'a pas confirmé.
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

describe("Member import client", () => {
  test("the route is declared and sends the file", () => {
    const client = code("src/lib/controlPlane.ts");
    expect(client).toContain(
      "importMembers(organizationId: string, file: File, mode: ImportMode, apply: boolean)",
    );
    expect(client).toContain("/members/import");
    expect(client).toContain("FormData");
  });
});

describe("Member import panel", () => {
  const page = code("src/pages/Members.tsx");

  test("a file is previewed first, then applied", async () => {
    const { t } = await import("./messages");
    expect(page).toContain("control!.importMembers(");
    expect(page).toContain("t.import.preview");
    expect(page).toContain("t.import.apply");
    expect(t.import.previewResult(120, 3, 118, 2).toLowerCase()).toContain("120");
  });

  test("both modes are offered, and what they do is said", async () => {
    const { t } = await import("./messages");
    expect(page).toContain('"add"');
    expect(page).toContain('"sync"');
    expect(t.import.modeAdd.toLowerCase()).toContain("add");
    expect(t.import.modeSync.toLowerCase()).toContain("remove");
    expect(t.import.nothingDeleted.toLowerCase()).toContain("account");
  });

  test("the expected columns are explained", async () => {
    const { t } = await import("./messages");
    const columns = t.import.columns.toLowerCase();
    expect(columns).toContain("email");
    expect(columns).toContain("group");
    expect(t.import.formats.toLowerCase()).toContain("xlsx");
  });

  test("refused lines are listed with their line number", async () => {
    const { t } = await import("./messages");
    expect(page).toContain("preview.invalid.map");
    expect(t.import.invalidLine(7, "Bad email")).toContain("7");
  });

  test("server refusals are explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("invalid", "IMPORT_EMAIL_COLUMN_MISSING")).toBe(
      t.import.emailColumnMissing,
    );
    expect(errorMessage("invalid", "IMPORT_FILE_UNSUPPORTED")).toBe(
      t.import.fileUnsupported,
    );
    expect(errorMessage("invalid", "IMPORT_TOO_MANY_ROWS")).toBe(t.import.tooManyRows);
    expect(errorMessage("invalid", "IMPORT_FILE_TOO_LARGE")).toBe(t.import.fileTooLarge);
  });
});
