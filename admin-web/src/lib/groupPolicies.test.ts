/**
 * Les réglages par groupe dans la console, vus depuis leurs garanties.
 *
 * Un groupe ne recopie pas les réglages de l'organisation : il en surcharge
 * quelques-uns. L'écran doit donc distinguer « suit l'organisation » de
 * « choisi pour ce groupe », n'envoyer que les surcharges, et dire la règle
 * qui départage deux groupes d'un même membre.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  applyChoice,
  choiceOf,
  countOverrides,
  dictationMinutes,
  withDictationMinutes,
} from "./groupPolicies";

const PAGE = readFileSync("src/pages/GroupPolicies.tsx", "utf8");
const REST = readFileSync("src/pages/Rest.tsx", "utf8");
const CONTROL = readFileSync("src/lib/controlPlane.ts", "utf8");

/** Le code, commentaires retirés : un mot dans un commentaire ne prouve rien. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

describe("Choices", () => {
  test("a setting the group does not set follows the organization", () => {
    expect(choiceOf({}, "ai_skills_enabled")).toBe("inherit");
    expect(choiceOf({ ai_skills_enabled: false }, "ai_skills_enabled")).toBe("off");
    expect(choiceOf({ ai_skills_enabled: true }, "ai_skills_enabled")).toBe("on");
  });

  test("following the organization removes the override; on and off set it", () => {
    expect(
      applyChoice({ ai_skills_enabled: false, learning_enabled: true }, "ai_skills_enabled", "inherit"),
    ).toEqual({ learning_enabled: true });
    expect(applyChoice({}, "styles_enabled", "off")).toEqual({ styles_enabled: false });
    expect(applyChoice({}, "styles_enabled", "on")).toEqual({ styles_enabled: true });
  });

  test("a choice never mutates the overrides it started from", () => {
    const before = { learning_enabled: false };
    applyChoice(before, "learning_enabled", "inherit");
    expect(before).toEqual({ learning_enabled: false });
  });

  test("the dictation limit is shown in minutes and stored in seconds", () => {
    expect(dictationMinutes({ max_dictation_seconds: 300 })).toBe(5);
    expect(dictationMinutes({})).toBeNull();
    expect(withDictationMinutes({}, 12)).toEqual({ max_dictation_seconds: 720 });
    expect(withDictationMinutes({ max_dictation_seconds: 720 }, null)).toEqual({});
  });

  test("overrides are counted for the group summary", () => {
    expect(countOverrides({})).toBe(0);
    expect(countOverrides({ learning_enabled: false, max_dictation_seconds: 300 })).toBe(2);
  });
});

describe("Client", () => {
  test("group settings have their own routes, with a revision on every write", () => {
    expect(CONTROL).toContain("/api/control/organization/policy/groups");
    expect(CONTROL).toMatch(/updateGroupPolicy\(\s*groupId: string,\s*expectedRevision: number/);
  });
});

describe("Page", () => {
  test("Configuration shows group settings next to the organization policies", () => {
    expect(code(REST)).toContain("<GroupPolicies");
    // La même table que les réglages de l'organisation : un réglage ajouté à
    // l'un apparaît dans l'autre.
    expect(code(REST)).toContain("catalog={POLICY_GROUPS}");
  });

  test("writing is gated on policy_manage, not on a role name", () => {
    expect(code(PAGE)).toContain("CAPABILITY.policyManage");
    expect(code(PAGE)).not.toContain('role === "organization_admin"');
  });

  test("a conflict is surfaced, never overwritten", () => {
    expect(code(PAGE)).toContain('raised.kind === "conflict"');
  });

  test("the rule between two groups is stated on screen", () => {
    expect(code(PAGE)).toContain("t.groupPolicies.restrictive");
  });

  test("the console offers typed controls, not a document editor", () => {
    for (const forbidden of ["JSON.parse", "textarea", "contentEditable"]) {
      expect(code(PAGE)).not.toContain(forbidden);
    }
  });
});

describe("Messages", () => {
  test("the restrictive rule is explained in plain words", async () => {
    const { t } = await import("./messages");
    expect(t.groupPolicies.restrictive.toLowerCase()).toContain("most restrictive");
  });

  test("server codes become readable sentences", async () => {
    const { errorMessage, t } = await import("./messages");
    expect(errorMessage("conflict", "GROUP_POLICY_REVISION_CONFLICT")).toBe(
      t.groupPolicies.conflict,
    );
    expect(errorMessage("not_found", "GROUP_NOT_FOUND")).toBe(t.groupPolicies.notFound);
  });
});
