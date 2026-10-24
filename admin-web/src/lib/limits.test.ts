/**
 * La page Limites : la durée maximale d'une dictée pour toute l'organisation.
 *
 * Le serveur accepte déjà ce réglage (30 secondes à 2 heures, 10 minutes par
 * défaut) et les groupes peuvent le surcharger, mais aucun écran ne permettait
 * de le changer pour l'organisation. On teste ce que la page envoie et ce
 * qu'elle refuse, plus que son rendu.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import {
  capProblem,
  rewriteCap,
  withRewriteCap,
  DICTATION_LIMIT_KEY,
  dictationLimitMinutes,
  limitProblem,
  withDictationLimit,
} from "./limits";
import { NAV_SECTIONS } from "./navigation";

const PAGE = readFileSync("src/pages/Limits.tsx", "utf8");
const APP = readFileSync("src/App.tsx", "utf8");

/** Le code, commentaires retirés : un mot dans un commentaire ne prouve rien. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

describe("Dictation limit model", () => {
  test("the limit is shown in minutes, the server default when never set", () => {
    expect(DICTATION_LIMIT_KEY).toBe("max_dictation_seconds");
    expect(dictationLimitMinutes({ max_dictation_seconds: 900 })).toBe(15);
    expect(dictationLimitMinutes({ ai_skills_enabled: true })).toBe(10);
  });

  test("changing the limit keeps every other setting of the document", () => {
    // Le PUT de la policy remplace tout le document : une clé oubliée
    // reprendrait son défaut et rouvrirait une fonction fermée.
    const settings = {
      ai_skills_enabled: false,
      styles_enabled: true,
      max_dictation_seconds: 600,
    };
    expect(withDictationLimit(settings, 25)).toEqual({
      ai_skills_enabled: false,
      styles_enabled: true,
      max_dictation_seconds: 1500,
    });
    expect(settings.max_dictation_seconds).toBe(600);
  });

  test("only whole minutes the server accepts can be saved", () => {
    expect(limitProblem("10")).toBeNull();
    expect(limitProblem("1")).toBeNull();
    expect(limitProblem("120")).toBeNull();
    expect(limitProblem("0")).toBe("range");
    expect(limitProblem("121")).toBe("range");
    expect(limitProblem("2.5")).toBe("whole");
    expect(limitProblem("")).toBe("empty");
    expect(limitProblem("abc")).toBe("empty");
  });
});

describe("Limits page", () => {
  test("it has its own domain in the sidebar, right after Content", async () => {
    const { t } = await import("./messages");
    const ids = NAV_SECTIONS.map((section) => section.id);
    expect(ids.indexOf("limits")).toBe(ids.indexOf("content") + 1);
    // Le domaine porte aussi le tableau de bord d'usage : les limites qu'on
    // fixe et l'usage qu'on mesure se lisent l'un apres l'autre. L'adoption,
    // le bilan par semaine et par mois, vient apres le detail horaire.
    expect(
      NAV_SECTIONS.find((section) => section.id === "limits")?.pages,
    ).toEqual(["limits", "usage", "adoption"]);
    expect(t.navGroups.limits).toBe("Limits");
    expect(code(APP)).toContain("<Limits />");
  });

  test("it reads the organization policy and writes it back whole, on the revision it showed", () => {
    const source = code(PAGE);
    expect(source).toContain("control.policy()");
    expect(source).toContain(
      "next={(value) => withDictationLimit(view.settings, value)}",
    );
    expect(source).toContain(
      "next={(value) => withRewriteCap(view.settings, value)}",
    );
    expect(source).toContain("revision={view.revision}");
    expect(source).toContain(
      "control!.updatePolicy(props.revision, props.next(Number(input)))",
    );
    const save = source.slice(source.indexOf("async function save()"));
    expect(save).toContain("props.onReload()");
  });

  test("editing is gated on policy_manage and a conflict is never overwritten", () => {
    const source = code(PAGE);
    expect(source).toContain("CAPABILITY.policyManage");
    expect(source).toContain('raised.kind === "conflict"');
    for (const forbidden of ["JSON.parse", "textarea", "contentEditable"]) {
      expect(source).not.toContain(forbidden);
    }
  });

  test("the page says what members experience and how groups change it", async () => {
    const { t } = await import("./messages");
    expect(t.limits.behaviour).toContain("30 seconds");
    expect(t.limits.behaviour.toLowerCase()).toContain("transcribe");
    expect(t.limits.groups.toLowerCase()).toContain("group");
    expect(t.limits.groups.toLowerCase()).toContain("shortest");
    expect(code(PAGE)).toContain("t.limits.behaviour");
    expect(code(PAGE)).toContain("t.limits.groups");
  });

  test("its wording names no backend key", async () => {
    const { t } = await import("./messages");
    for (const value of Object.values(t.limits)) {
      if (typeof value === "string") expect(value).not.toContain("_");
    }
  });
});

describe("daily rewrite cap", () => {
  test("no cap until the organization sets one", () => {
    expect(rewriteCap({})).toBe(0);
  });

  test("the whole document is sent with the cap", () => {
    expect(withRewriteCap({ learning_enabled: false }, 40)).toEqual({
      learning_enabled: false,
      daily_rewrites_per_member: 40,
    });
  });

  test("a cap is a whole number from 0", () => {
    expect(capProblem("0")).toBeNull();
    expect(capProblem("2.5")).toBe("whole");
    expect(capProblem("-1")).toBe("range");
    expect(capProblem("")).toBe("empty");
  });
});
