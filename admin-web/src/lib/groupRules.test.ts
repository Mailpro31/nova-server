/**
 * Les règles de tri, vues depuis la console.
 *
 * Écrire une règle sans savoir qui elle range revient à parier : la page
 * montre donc un aperçu avant d'enregistrer. Elle dit aussi ce qu'une règle a
 * rangé, et pourquoi un membre venu d'une règle ne se retire pas à la main.
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

describe("Group rules client", () => {
  test("every route the page needs is declared", () => {
    const client = code("src/lib/controlPlane.ts");
    for (const method of [
      "groupRules(organizationId: string)",
      "createGroupRule(organizationId: string, body: GroupRuleBody)",
      "updateGroupRule(organizationId: string, ruleId: string, body: GroupRuleBody)",
      "deleteGroupRule(organizationId: string, ruleId: string)",
      "previewGroupRule(organizationId: string, body: GroupRuleBody)",
      "applyGroupRules(organizationId: string)",
    ]) {
      expect(client).toContain(method);
    }
    expect(client).toContain("/group-rules");
    // L'origine d'une appartenance : la page s'en sert pour expliquer.
    expect(client).toContain('origin: "manual" | "rule"');
  });
});

describe("Group rules page", () => {
  const page = code("src/pages/Groups.tsx");

  test("a rule is written, previewed, saved and deleted", async () => {
    const { t } = await import("./messages");
    expect(page).toContain("control!.previewGroupRule(");
    expect(page).toContain("control!.createGroupRule(");
    expect(page).toContain("control!.updateGroupRule(");
    expect(page).toContain("control!.deleteGroupRule(");
    expect(page).toContain("control!.applyGroupRules(");
    expect(t.rules.previewResult(7, ["a@example.com"]).toLowerCase()).toContain("7");
  });

  test("the fields and operators come from the server, not from the page", () => {
    expect(page).toContain("view.fields.map");
    expect(page).toContain("view.operators.map");
  });

  test("every field and operator has a sentence a human reads", async () => {
    const { t } = await import("./messages");
    for (const field of ["department", "organization", "email_domain", "member_type"]) {
      expect(t.rules.fieldLabels[field]?.length).toBeGreaterThan(0);
    }
    for (const operator of ["equals", "contains", "starts_with"]) {
      expect(t.rules.operatorLabels[operator]?.length).toBeGreaterThan(0);
    }
  });

  test("a member sorted by a rule is shown as such and cannot be removed by hand", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(page).toContain('member.origin === "rule"');
    expect(t.rules.memberByRule.toLowerCase()).toContain("rule");
    expect(errorMessage("conflict", "GROUP_MEMBER_ADDED_BY_RULE")).toBe(
      t.rules.memberByRule,
    );
  });

  test("server refusals are explained", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("invalid", "RULE_FIELD_INVALID")).toBe(t.rules.fieldInvalid);
    expect(errorMessage("invalid", "RULE_VALUE_INVALID")).toBe(t.rules.valueInvalid);
    expect(errorMessage("conflict", "RULE_LIMIT_REACHED")).toBe(t.rules.limitReached);
  });

  test("rules only fill groups created in Nova Admin", async () => {
    const { t } = await import("./messages");
    expect(t.rules.consoleGroupsOnly.toLowerCase()).toContain("directory");
    expect(page).toContain("t.rules.consoleGroupsOnly");
  });
});
