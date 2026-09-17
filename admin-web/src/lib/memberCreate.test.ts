/**
 * Ajouter un membre depuis la console, avant sa première connexion.
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

describe("Adding a member", () => {
  test("the client posts the new member to the organization", () => {
    expect(code("src/lib/controlPlane.ts")).toMatch(
      /createMember\(\s*organizationId: string,\s*body: \{ email: string/,
    );
  });

  test("the page offers it to those who manage identity", async () => {
    const { t } = await import("./messages");
    const page = code("src/pages/Members.tsx");
    expect(page).toContain("control!.createMember(");
    expect(page).toContain("t.members.addMember");
    expect(page).toContain("CAPABILITY.identityManage");
    expect(t.members.addMemberLead.toLowerCase()).toContain("first sign-in");
  });

  test("server refusals are explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("invalid", "MEMBER_EMAIL_OUTSIDE_DOMAINS")).toBe(t.members.outsideDomains);
    expect(errorMessage("invalid", "MEMBER_EMAIL_INVALID")).toBe(t.members.emailInvalid);
    expect(errorMessage("conflict", "MEMBER_ALREADY_EXISTS")).toBe(t.members.alreadyExists);
  });
});
