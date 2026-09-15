/**
 * Le journal d'audit dit ce qui a changé, pas seulement qu'une action a eu lieu.
 *
 * Les lignes qui portent `before` et `after` montrent chaque valeur modifiée ;
 * celles qui ne portent que `changed_keys` nomment les réglages modifiés. La
 * console ne devine rien : ce qui n'est pas dans la ligne ne s'affiche pas.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { auditChanges, formatAuditValue } from "./audit";

const REST = readFileSync("src/pages/Rest.tsx", "utf8");

describe("Changes read from a row", () => {
  test("before and after give one line per value that changed", () => {
    expect(
      auditChanges({
        before: { required: false, due_at: null, group_ids: [] },
        after: { required: true, due_at: null, group_ids: ["g1"] },
      }),
    ).toEqual([
      { field: "group_ids", before: [], after: ["g1"] },
      { field: "required", before: false, after: true },
    ]);
  });

  test("a creation has no before, a deletion no after", () => {
    expect(auditChanges({ before: null, after: { titles: { en: "Rules" } } })).toEqual([
      { field: "titles", before: undefined, after: { en: "Rules" } },
    ]);
    expect(auditChanges({ before: { difficulty: "beginner" }, after: null })).toEqual([
      { field: "difficulty", before: "beginner", after: undefined },
    ]);
  });

  test("changed keys alone are named, without inventing values", () => {
    expect(auditChanges({ changed_keys: ["ai_skills_enabled"] })).toEqual([
      { field: "ai_skills_enabled", before: undefined, after: undefined },
    ]);
  });

  test("a row without changes shows none", () => {
    expect(auditChanges(null)).toEqual([]);
    expect(auditChanges({ previous_role: "member" })).toEqual([]);
  });
});

describe("Values", () => {
  test("values are shown the way an administrator reads them", () => {
    expect(formatAuditValue(true)).toBe("Enabled");
    expect(formatAuditValue(false)).toBe("Disabled");
    expect(formatAuditValue(undefined)).toBe("—");
    expect(formatAuditValue(null)).toBe("—");
    expect(formatAuditValue(["g1", "g2"])).toBe("g1, g2");
    expect(formatAuditValue([])).toBe("—");
    expect(formatAuditValue({ en: "Rules", fr: "Règles" })).toBe("en: Rules, fr: Règles");
    expect(formatAuditValue(300)).toBe("300");
  });
});

describe("Journal", () => {
  test("the activity list shows the changes of each row", () => {
    expect(REST).toContain("auditChanges(entry.metadata)");
    expect(REST).toContain("formatAuditValue(");
  });
});
