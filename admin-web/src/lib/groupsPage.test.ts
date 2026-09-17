/**
 * La page Groupes : créer, renommer, supprimer, gérer les membres.
 *
 * Un groupe de l'annuaire se montre sans bouton de modification : l'annuaire
 * en reste le seul auteur.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { NAV_SECTIONS } from "./navigation";

function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

describe("Groups client", () => {
  test("every group route the page needs is declared", () => {
    const client = code("src/lib/controlPlane.ts");
    for (const method of [
      "groups(organizationId: string)",
      "createGroup(organizationId: string, displayName: string)",
      "renameGroup(organizationId: string, groupId: string, displayName: string)",
      "deleteGroup(organizationId: string, groupId: string)",
      "groupMembers(organizationId: string, groupId: string)",
      "addGroupMember(organizationId: string, groupId: string, userId: string)",
      "removeGroupMember(organizationId: string, groupId: string, userId: string)",
    ]) {
      expect(client).toContain(method);
    }
  });
});

describe("Groups page", () => {
  const page = code("src/pages/Groups.tsx");

  test("it lives with the people, after Members", async () => {
    const { t } = await import("./messages");
    const people = NAV_SECTIONS.find((section) => section.id === "people");
    expect(people?.pages.slice(0, 2)).toEqual(["members", "groups"]);
    expect(t.nav.groups).toBe("Groups");
    expect(code("src/App.tsx")).toContain("<Groups />");
  });

  test("groups are created, renamed and deleted behind a danger confirmation", async () => {
    const { t } = await import("./messages");
    expect(page).toContain("control!.createGroup(");
    expect(page).toContain("control!.renameGroup(");
    expect(page).toContain("control!.deleteGroup(");
    expect(page).toContain("t.groups.deleteTitle");
    expect(page).toContain('tone="danger"');
    expect(t.groups.deleteBody.toLowerCase()).toContain("settings");
  });

  test("members are found by email, added and removed", () => {
    expect(page).toContain("control.members(organizationId, { query");
    expect(page).toContain("control!.addGroupMember(");
    expect(page).toContain("control!.removeGroupMember(");
  });

  test("a directory group is read-only", () => {
    expect(page).toContain('group.source === "console"');
    expect(page).toContain("t.groups.managedByDirectory");
  });

  test("editing is gated on identity management", () => {
    expect(page).toContain("CAPABILITY.identityManage");
  });

  test("server refusals are explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("conflict", "GROUP_NAME_TAKEN")).toBe(
      t.groups.nameTaken,
    );
    expect(errorMessage("invalid", "GROUP_NAME_INVALID")).toBe(
      t.groups.nameInvalid,
    );
    expect(errorMessage("conflict", "GROUP_MANAGED_BY_DIRECTORY")).toBe(
      t.groups.managedByDirectory,
    );
  });
});
