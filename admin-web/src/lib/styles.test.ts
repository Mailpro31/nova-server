/**
 * Catégories de la barre latérale et Styles désactivés.
 *
 * Chaque catégorie de la barre latérale de Nova, sauf Accueil et Réglages, se
 * ferme depuis Configuration, sous le nom que le membre lit dans sa barre.
 * Un Style se désactive aussi seul : Nova le montre barré d'un bandeau rouge et
 * ne l'applique plus.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  BUILTIN_STYLES,
  DISABLED_STYLES_KEY,
  disabledStyleIds,
  organizationStyleId,
  stylesCatalog,
  withStyleDisabled,
} from "./styles";

const REST = readFileSync("src/pages/Rest.tsx", "utf8");
const PANEL = readFileSync("src/pages/DisabledStyles.tsx", "utf8");

/** Le code, commentaires retirés : un mot dans un commentaire ne prouve rien. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

describe("Styles model", () => {
  test("the builtin Styles are those an organization member sees", () => {
    expect(BUILTIN_STYLES.map((style) => style.id)).toEqual([
      "default_improve_transcriptions",
      "nova_style_course_notes",
      "nova_style_email",
      "nova_style_messages",
      "nova_style_notes",
      "nova_style_todo",
      "nova_style_prompt",
      "nova_style_voice_to_text",
    ]);
    for (const style of BUILTIN_STYLES) expect(style.name).toBeTruthy();
  });

  test("an organization Style is named the way the workstation names it", () => {
    expect(organizationStyleId("abc123")).toBe("org_style_abc123");
  });

  test("disabling keeps every other setting and a sorted list without duplicates", () => {
    const settings = { ai_skills_enabled: false, max_dictation_seconds: 600 };
    const once = withStyleDisabled(settings, "nova_style_prompt", true);
    const twice = withStyleDisabled(once, "nova_style_email", true);
    expect(twice).toEqual({
      ai_skills_enabled: false,
      max_dictation_seconds: 600,
      [DISABLED_STYLES_KEY]: ["nova_style_email", "nova_style_prompt"],
    });
    expect(
      withStyleDisabled(twice, "nova_style_email", true)[DISABLED_STYLES_KEY],
    ).toEqual(["nova_style_email", "nova_style_prompt"]);
    expect(
      withStyleDisabled(twice, "nova_style_prompt", false)[DISABLED_STYLES_KEY],
    ).toEqual(["nova_style_email"]);
    expect(disabledStyleIds(settings)).toEqual([]);
  });

  test("the catalogue lists builtin Styles, then the organization's published Styles", () => {
    const catalog = stylesCatalog([
      {
        package_id: "p1",
        package_type: "style",
        name: "House email",
        status: "published",
      },
      {
        package_id: "p2",
        package_type: "ai_skill",
        name: "Summarize",
        status: "published",
      },
      {
        package_id: "p3",
        package_type: "style",
        name: "Old style",
        status: "archived",
      },
    ]);
    expect(
      catalog
        .slice(0, BUILTIN_STYLES.length)
        .every((style) => style.origin === "builtin"),
    ).toBe(true);
    expect(catalog.slice(BUILTIN_STYLES.length)).toEqual([
      { id: "org_style_p1", name: "House email", origin: "organization" },
    ]);
  });
});

describe("Styles panel", () => {
  test("it lives in Configuration", () => {
    expect(code(REST)).toContain("<DisabledStyles />");
  });

  test("it writes the whole policy back on the revision it showed", () => {
    const source = code(PANEL);
    expect(source).toContain("control.policy()");
    expect(source).toContain("withStyleDisabled(");
    expect(source).toContain("control!.updatePolicy(view.revision, next)");
    expect(source).toContain('raised.kind === "conflict"');
    expect(source).toContain("retry()");
  });

  test("only policy_manage may change it", () => {
    expect(code(PANEL)).toContain("CAPABILITY.policyManage");
  });

  test("it says what members see", async () => {
    const { t } = await import("./messages");
    expect(t.styles.lead.toLowerCase()).toContain("disabled");
    expect(t.styles.lead.toLowerCase()).toContain("another style");
    expect(code(PANEL)).toContain("t.styles.lead");
    for (const value of Object.values(t.styles)) {
      if (typeof value === "string") expect(value).not.toContain("_");
    }
  });
});

describe("Sidebar categories", () => {
  test("every closable category of the Nova sidebar is a switch, under its sidebar name", async () => {
    const { t } = await import("./messages");
    const source = code(REST);
    for (const key of [
      "learning_enabled",
      "voice_commands_enabled",
      "ai_skills_enabled",
      "styles_enabled",
      "generated_instructions_enabled",
      "engineering_notes_enabled",
      "past_dictations_enabled",
    ]) {
      expect(source).toContain(`key: "${key}"`);
    }
    expect(t.policies.groupSidebar).toBe("Sidebar categories");
    expect(t.policies.commandsTitle).toBe("Nova Commands");
    expect(t.policies.notesTitle).toBe("Structured notes");
    expect(t.policies.sidebarNote.toLowerCase()).toContain("sidebar");
  });
});
