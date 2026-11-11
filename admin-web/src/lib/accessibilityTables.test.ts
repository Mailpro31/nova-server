/**
 * Accessibilité, lot 5 (demande de Sash du 11/11).
 *
 * - Chaque tableau porte un nom (aria-label) et ses en-têtes de colonne disent
 *   ce qu'ils sont (scope="col") : un lecteur d'écran annonce sinon « tableau,
 *   six colonnes » sans dire lequel.
 * - Les petits textes gris restent lisibles : contraste d'au moins 4,5:1
 *   (WCAG AA) sur le fond, en clair comme en sombre.
 */

import { describe, expect, test } from "bun:test";
import { readdirSync, readFileSync } from "node:fs";

const CSS = readFileSync("src/styles.css", "utf8");

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
function token(block: string, name: string): string {
  const m = block.match(new RegExp(String.raw`--${name}:\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`jeton ${name} introuvable`);
  return m[1];
}

describe("tableaux", () => {
  for (const file of readdirSync("src/pages").filter((f) => f.endsWith(".tsx"))) {
    const source = readFileSync(`src/pages/${file}`, "utf8");
    const tables = source.match(/<table\b[^>]*>/g) ?? [];
    if (tables.length === 0) continue;
    test(`${file} : chaque tableau a un nom`, () => {
      for (const tag of tables) expect(tag).toMatch(/aria-label=/);
    });
    test(`${file} : chaque en-tête de colonne a scope="col"`, () => {
      const heads = source.match(/<th\b[^>]*>/g) ?? [];
      for (const tag of heads) expect(tag).toMatch(/scope="(col|row)"/);
    });
  }
});

describe("contraste des textes pâles", () => {
  const [light, dark] = CSS.split("@media (prefers-color-scheme: dark)");
  test("thème clair", () => {
    expect(contrast(token(light, "text-faint"), token(light, "bg"))).toBeGreaterThanOrEqual(4.5);
  });
  test("thème sombre", () => {
    expect(contrast(token(dark, "text-faint"), token(dark, "bg"))).toBeGreaterThanOrEqual(4.5);
  });
});
