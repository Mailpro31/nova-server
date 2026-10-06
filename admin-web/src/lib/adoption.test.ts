/**
 * L'adoption : on teste ce que la direction lira, pas le rendu. Une période
 * masquée ne doit jamais passer pour un zéro, ni à l'écran ni dans le CSV.
 */

import { describe, expect, test } from "bun:test";
import {
  adoptionCsv,
  latestWeek,
  monthLabel,
  weekLabel,
  type AdoptionReport,
} from "./adoption";

const MASKED = {
  people: null,
  regular_people: null,
  dictations: null,
  reformulations: null,
  audio_minutes: null,
  masked: true,
};

const REPORT: AdoptionReport = {
  min_people: 3,
  regular_days: 3,
  weeks: [
    {
      week_start: "2026-09-28",
      people: 4,
      regular_people: 2,
      dictations: 120,
      reformulations: 30,
      audio_minutes: 45.5,
      masked: false,
    },
    { week_start: "2026-10-05", ...MASKED },
  ],
  months: [
    {
      month: "2026-09",
      people: 5,
      dictations: 300,
      reformulations: 60,
      audio_minutes: 110,
      masked: false,
    },
  ],
};

describe("adoption", () => {
  test("les libellés de semaine et de mois", () => {
    expect(weekLabel("2026-10-05")).toBe("Week of Oct 5");
    expect(monthLabel("2026-09")).toBe("Sep 2026");
  });

  test("la dernière semaine lisible saute une semaine masquée", () => {
    expect(latestWeek(REPORT)?.week_start).toBe("2026-09-28");
    expect(latestWeek({ ...REPORT, weeks: [REPORT.weeks[1]] })).toBeNull();
  });

  test("le CSV dit qu'une période est masquée, sans zéro inventé", () => {
    const lines = adoptionCsv(REPORT).split("\n");
    expect(lines[0]).toBe(
      "periode;debut;personnes;personnes_regulieres;dictees;reformulations;minutes_dictees;masque",
    );
    expect(lines[1]).toBe("semaine;2026-09-28;4;2;120;30;45.5;");
    expect(lines[2]).toBe("semaine;2026-10-05;;;;;;moins de 3 personnes");
    expect(lines[3]).toBe("mois;2026-09;5;;300;60;110;");
  });
});
