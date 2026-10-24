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
