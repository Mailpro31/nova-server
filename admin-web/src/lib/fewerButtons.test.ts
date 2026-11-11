/**
 * Moins de boutons (lot 6, demande de Sash du 11/11 : « trop de boutons
 * partout, simplifie »).
 *
 * Sur chaque ligne d'une liste, une seule action reste visible ; les actions
 * rares (archiver, restaurer, supprimer, renommer, personnaliser) passent dans
 * un menu « Plus d'actions ».
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { fr } from "./messages.fr";

const PRIMITIVES = readFileSync("src/components/primitives.tsx", "utf8");

/** Le texte compris dans les menus « Plus d'actions » d'une page. */
function inMenus(file: string): string {
  const source = readFileSync(`src/pages/${file}`, "utf8");
  return [...source.matchAll(/<MoreActions[^>]*>([\s\S]*?)<\/MoreActions>/g)].map((m) => m[1]).join("\n");
}

describe("menu Plus d'actions", () => {
  test("un composant commun, en français", () => {
    expect(PRIMITIVES).toContain("export function MoreActions");
    expect(PRIMITIVES).toContain("<details");
    expect(fr.moreActions).toBe("Plus d’actions");
  });

  test("Apprendre : personnaliser, archiver et restaurer sont dans le menu", () => {
    const menu = inMenus("Learn.tsx");
    for (const label of ["t.learn.customize", "t.learn.archive", "t.learn.restore"]) expect(menu).toContain(label);
  });

  test("Groupes : renommer et supprimer sont dans le menu", () => {
    const menu = inMenus("Groups.tsx");
    for (const label of ["t.groups.rename", "t.groups.delete"]) expect(menu).toContain(label);
  });

  test("Contenus : archiver, restaurer et supprimer sont dans le menu", () => {
    const menu = inMenus("Packages.tsx");
    for (const label of ["t.packages.archive", "t.packages.restore", "t.packages.delete"]) expect(menu).toContain(label);
  });
});
