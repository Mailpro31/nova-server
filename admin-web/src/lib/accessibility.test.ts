/**
 * L'accessibilité au clavier de la console (lot 1, 11/11).
 *
 * Un lien « Aller au contenu » permet de sauter les dix-sept liens de la barre
 * latérale : sans lui, chaque changement de page oblige à retraverser toute la
 * navigation à la touche Tab avant d'atteindre le premier champ.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const APP = readFileSync("src/App.tsx", "utf8");
const CSS = readFileSync("src/styles.css", "utf8");
const FR = readFileSync("src/lib/messages.fr.ts", "utf8");

describe("lien d'évitement", () => {
  test("un lien mène au contenu principal, qui peut recevoir le focus", () => {
    expect(APP).toMatch(/<a\s[^>]*className="skip-link"[^>]*href="#contenu"|<a\s[^>]*href="#contenu"[^>]*className="skip-link"/);
    expect(APP).toMatch(/<main[^>]*\bid="contenu"/);
    expect(APP).toMatch(/<main[^>]*\btabIndex=\{-1\}/);
  });

  test("il n'apparaît qu'au clavier", () => {
    expect(CSS).toMatch(/\.skip-link\s*\{/);
    expect(CSS).toMatch(/\.skip-link:focus/);
  });

  test("son libellé est traduit", () => {
    expect(FR).toMatch(/skipToContent:\s*"Aller au contenu"/);
  });
});
