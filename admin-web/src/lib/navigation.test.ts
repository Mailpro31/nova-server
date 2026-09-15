/**
 * La navigation de la console, rangée par domaines.
 *
 * Onze pages alignées dans une seule liste obligeaient à savoir où chercher :
 * les Styles sous « Packages », les réglages des groupes sous
 * « Configuration », le journal au bas de « Security ». La barre latérale suit
 * désormais les domaines d'une organisation, dans l'ordre où on les
 * administre, et garde le style de Nova Desktop.
 *
 * Les adresses des pages ne changent pas : un lien partagé reste valable.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { NAV_SECTIONS } from "./navigation";

const APP = readFileSync("src/App.tsx", "utf8");
const CSS = readFileSync("src/styles.css", "utf8");

/** Le code, commentaires retirés : un mot dans un commentaire ne prouve rien. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !line.trim().startsWith("//"))
    .join("\n");
}

const routedPages = [...code(APP).matchAll(/path: "([a-z-]+)"/g)].map((match) => match[1]);

describe("Domains", () => {
  test("the sidebar follows the organization's domains, in order", () => {
    expect(NAV_SECTIONS.map((section) => section.id)).toEqual([
      "overview",
      "people",
      "features",
      "learn",
      "content",
      "limits",
      "signIn",
      "audit",
      "infrastructure",
    ]);
  });

  test("every routed page belongs to exactly one domain", () => {
    const listed = NAV_SECTIONS.flatMap((section) => section.pages);
    expect(new Set(listed).size).toBe(listed.length);
    expect([...listed].sort()).toEqual([...routedPages].sort());
  });

  test("pages keep the addresses they had", () => {
    for (const path of [
      "overview",
      "members",
      "administrators",
      "provisioning",
      "configuration",
      "learn",
      "packages",
      "identity",
      "security",
      "deployment",
      "diagnostics",
    ]) {
      expect(routedPages).toContain(path);
    }
  });

  test("every domain has a name, and none points at member content", async () => {
    const { t } = await import("./messages");
    for (const section of NAV_SECTIONS) {
      expect(t.navGroups[section.id]).toBeTruthy();
    }
    const names = Object.values(t.navGroups).join(" ").toLowerCase();
    for (const forbidden of ["transcript", "recording", "audio", "prompt", "activity", "history", "usage"]) {
      expect(names).not.toContain(forbidden);
    }
  });

  test("the sidebar is drawn from the domain table", () => {
    expect(code(APP)).toContain("NAV_SECTIONS.map");
  });
});

describe("Audit log", () => {
  test("the audit log has its own page instead of closing the Security page", async () => {
    const { t } = await import("./messages");
    expect(t.nav.audit).toBe("Audit log");
    expect(code(APP)).toContain('path: "audit"');
    expect(code(APP)).toContain("<AuditLog />");
  });
});

describe("Look", () => {
  test("the console uses Nova Desktop's colors and font, dark and light", () => {
    for (const token of ["#1f1f22", "#28282c", "#0a84ff", "#f5f5f7", "#ececee", "Segoe UI Variable Text"]) {
      expect(CSS).toContain(token);
    }
  });
});
