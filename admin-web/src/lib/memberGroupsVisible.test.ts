/**
 * Ce que le membre voit de ses groupes, réglé depuis la console.
 *
 * Quatre niveaux : l'organisation et chaque groupe (le même réglage, dans le
 * panneau des réglages et ses surcharges de groupe), une personne (sa fiche),
 * et un groupe précis retiré de l'affichage (la page Groupes).
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

describe("Member groups display", () => {
  test("the organization and group settings offer the switch", async () => {
    const { t } = await import("./messages");
    const rest = code("src/pages/Rest.tsx");
    expect(rest).toContain('key: "member_groups_visible"');
    expect(rest).toContain("t.policies.memberGroupsTitle");
    expect(t.policies.memberGroupsBody.toLowerCase()).toContain("nova desktop");
  });

  test("the client declares both routes", () => {
    const client = code("src/lib/controlPlane.ts");
    expect(client).toContain(
      "setGroupVisibility(organizationId: string, groupId: string, visibleToMembers: boolean)",
    );
    expect(client).toContain("/visibility");
    expect(client).toContain("memberGroupDisplay(organizationId: string, userId: string)");
    expect(client).toContain(
      "setMemberGroupDisplay(organizationId: string, userId: string, visible: boolean | null)",
    );
    expect(client).toContain("/group-display");
    expect(client).toContain("visible_to_members: boolean;");
  });

  test("a group can be hidden from its members, directory groups included", async () => {
    const { t } = await import("./messages");
    const page = code("src/pages/Groups.tsx");
    expect(page).toContain("control!.setGroupVisibility(");
    expect(page).toContain("group.visible_to_members");
    // Pas réservé aux groupes de la console : c'est un choix d'affichage.
    expect(page).toContain("mayManage && (");
    expect(t.groups.visibleToMembers.length).toBeGreaterThan(0);
    expect(t.groups.hiddenFromMembers.length).toBeGreaterThan(0);
  });

  test("a person gets their own choice, which wins over the rest", async () => {
    const { t } = await import("./messages");
    const page = code("src/pages/Members.tsx");
    expect(page).toContain("control.memberGroupDisplay(");
    expect(page).toContain("control!.setMemberGroupDisplay(");
    expect(page).toContain("t.members.groupDisplayInherit");
    expect(page).toContain("t.members.groupDisplayShow");
    expect(page).toContain("t.members.groupDisplayHide");
    expect(t.members.groupDisplayHint.toLowerCase()).toContain("wins");
  });
});
