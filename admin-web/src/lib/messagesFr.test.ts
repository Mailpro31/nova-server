import { describe, expect, test } from "bun:test";

import { t } from "./messages";
import { fr } from "./messages.fr";

/**
 * La console en français (n° 28) : le DPO, la DSI et la mission handicap
 * d'une école la lisent. Rien ne doit y rester en anglais, sauf les noms qui
 * ne se traduisent pas.
 */
const SAME_IN_FRENCH = new Set([
  "Nova Admin", "Learn", "AI Skills", "AI Skill", "Styles", "Style", "API",
  "SHA-256", "Version", "Versions", "Configuration", "Description", "Type",
  "Module", "Distribution", "Architecture", "Usage", "Adoption", "Quiz",
  "Microsoft Entra ID", "Google Workspace", "Prompts", "Stable", "Signature",
  "Message", "Note", "Question", "Minutes", "Organisation", "Active", "Mode", "—",
]);

function strings(value: unknown, path = ""): [string, string][] {
  if (typeof value === "string") return [[path, value]];
  if (Array.isArray(value))
    return value.flatMap((item, index) => strings(item, `${path}[${index}]`));
  if (value && typeof value === "object")
    return Object.entries(value).flatMap(([key, item]) =>
      strings(item, path ? `${path}.${key}` : key),
    );
  return [];
}

describe("console in French", () => {
  test("the tests read the English catalogue", () => {
    expect(t.errors.network).toBe(
      "Cannot reach the Nova service. Check your connection and try again.",
    );
  });

  test("no text is left in English", () => {
    const english = new Map(strings(t));
    const left = strings(fr)
      .filter(([path, text]) => english.get(path) === text)
      .filter(([, text]) => !SAME_IN_FRENCH.has(text))
      .map(([path]) => path);
    expect(left).toEqual([]);
  });

  test("functions answer in French", () => {
    expect(fr.limits.minutes(3)).toBe("3 minutes");
    expect(fr.groups.memberCount(2)).toBe("2 membres");
  });
});

describe("singulier des durées et des effectifs", () => {
  test("un jour, une personne, un membre", () => {
    expect(fr.subscription.alerts.warning(1)).toContain("dans 1 jour.");
    expect(fr.subscription.alerts.grace(1)).toContain("encore 1 jour ;");
    expect(fr.subscription.gauge(1, 50)).toStartWith("1 personne sur");
    expect(fr.members.count(1, 12)).toBe("1 membre sur 12");
    expect(t.subscription.alerts.warning(1)).not.toContain("1 days");
  });
});
