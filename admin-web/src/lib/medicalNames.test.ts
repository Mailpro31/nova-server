import { expect, test } from "bun:test";

import { looksMedical } from "./medicalNames";

test("un nom qui dit un diagnostic est repéré", () => {
  for (const name of ["Dys L3", "Étudiants dyslexiques", "Tiers temps MDPH", "TDAH", "Mission handicap", "Groupe PAP", "Aménagements d'examen"]) {
    expect(looksMedical(name)).toBe(true);
  }
});

test("un nom ordinaire ne l'est pas", () => {
  for (const name of ["L3 Aéronautique", "Promo 2027", "Dynamique des fluides", "Papeterie", "Santiago"]) {
    expect(looksMedical(name)).toBe(false);
  }
});
