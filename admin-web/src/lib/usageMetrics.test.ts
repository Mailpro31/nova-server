/**
 * Le tableau de bord d'usage : ce qui se calcule avant de se dessiner.
 *
 * On teste les décisions, pas le rendu : l'échelle qui ne divise jamais par
 * zéro, le trou qui reste un trou, le CSV qu'Excel sait ouvrir en français, et
 * la carte graphique absente qui ne se lit pas comme une carte au repos.
 */

import { describe, expect, test } from "bun:test";
import {
  LATENCY_OBJECTIVE_MS,
  PLOT,
  WINDOWS,
  axisLabels,
  axisMaximum,
  bars,
  hourLabel,
  linePath,
  membersCsv,
  sampleLabel,
  seconds,
  share,
  timelineCsv,
  withoutGraphicsCard,
  type HostSample,
  type UsageHour,
} from "./usageMetrics";

function hour(overrides: Partial<UsageHour> = {}): UsageHour {
  return {
    hour: "2026-05-12T14",
    transcriptions: 1,
    reformulations: 2,
    audio_seconds: 30,
    people: 3,
    latency_ms_average: 1200,
    latency_ms_max: 1800,
    ...overrides,
  };
}

function sample(overrides: Partial<HostSample> = {}): HostSample {
  return {
    sampled_at: "2026-05-12T14:03:00",
    cpu_percent: 20,
    ram_used_gb: 8,
    ram_total_gb: 32,
    gpu_percent: 40,
    vram_used_gb: 3,
    vram_total_gb: 6,
    gpu_temperature_c: 60,
    gpu_power_w: 70,
    ...overrides,
  };
}

describe("échelle", () => {
  test("une série vide ne divise jamais par zéro", () => {
    expect(axisMaximum([])).toBe(1);
    expect(axisMaximum([null, null])).toBe(1);
    expect(axisMaximum([0, 0])).toBe(1);
  });

  test("le maximum est arrondi vers le haut pour laisser respirer", () => {
    expect(axisMaximum([7])).toBe(7);
    expect(axisMaximum([12])).toBe(20);
    expect(axisMaximum([1, 140, 3])).toBe(200);
  });

  test("les valeurs absentes ne comptent pas dans le maximum", () => {
    expect(axisMaximum([5, null, 3])).toBe(5);
  });
});

describe("tracé", () => {
  test("une heure sans mesure coupe la ligne au lieu de valoir zéro", () => {
    const path = linePath([10, null, 10], 10);
    // Deux départs de tracé : la ligne ne traverse pas le trou.
    expect(path.split("M").length - 1).toBe(2);
    expect(path).not.toContain("L");
  });

  test("une série continue ne fait qu'un seul tracé", () => {
    const path = linePath([1, 2, 3], 3);
    expect(path.split("M").length - 1).toBe(1);
    expect(path.split("L").length - 1).toBe(2);
  });

  test("le maximum touche le haut du repère et zéro le bas", () => {
    const path = linePath([0, 10], 10);
    expect(path).toContain(`M0 ${PLOT.height}`);
    expect(path).toContain("L720 0");
  });

  test("une série vide ne trace rien", () => {
    expect(linePath([], 10)).toBe("");
  });

  test("un point unique est place au depart du repere", () => {
    expect(linePath([5], 10)).toBe(`M0 ${PLOT.height / 2}`);
  });
});

describe("barres", () => {
  test("chaque heure a sa barre, dans l'ordre", () => {
    const drawn = bars([1, 2], 2);
    expect(drawn).toHaveLength(2);
    expect(drawn[0].x).toBeLessThan(drawn[1].x);
    expect(drawn[1].height).toBe(PLOT.height);
  });

  test("une heure sans dictée reste une barre de hauteur nulle", () => {
    expect(bars([0], 5)[0].height).toBe(0);
  });
});

describe("libellés", () => {
  test("l'heure seule sur un jour, la date au-delà", () => {
    expect(hourLabel("2026-05-12T14", false)).toBe("14 h");
    expect(hourLabel("2026-05-12T14", true)).toBe("12/05 14 h");
  });

  test("minuit se lit zéro et non vide", () => {
    expect(hourLabel("2026-05-12T00", false)).toBe("0 h");
  });

  test("au plus six libellés, premier et dernier compris", () => {
    const hours = Array.from({ length: 24 }, (_, i) =>
      `2026-05-12T${String(i).padStart(2, "0")}`,
    );
    const labels = axisLabels(hours, false);
    expect(labels).toHaveLength(6);
    expect(labels[0]).toBe("0 h");
    expect(labels[5]).toBe("23 h");
  });

  test("une fenêtre vide n'affiche aucun libellé", () => {
    expect(axisLabels([], false)).toEqual([]);
  });

  test("un relevé machine se lit à la minute", () => {
    expect(sampleLabel("2026-05-12T14:03:00")).toBe("14:03");
  });
});

describe("mise en forme", () => {
  test("une durée se lit en secondes, virgule française", () => {
    expect(seconds(1234)).toBe("1,2 s");
    expect(seconds(null)).toBe("—");
  });

  test("une mémoire absente ne vaut pas zéro pour cent", () => {
    expect(share(3, 6)).toBe(50);
    expect(share(null, 6)).toBeNull();
    expect(share(3, null)).toBeNull();
    expect(share(3, 0)).toBeNull();
  });

  test("une carte absente se reconnaît sur toute la fenêtre", () => {
    expect(withoutGraphicsCard([sample({ gpu_percent: null })])).toBe(true);
    expect(withoutGraphicsCard([sample({ gpu_percent: null }), sample()])).toBe(false);
    // Une fenêtre sans relevé n'affirme pas qu'il y a une carte.
    expect(withoutGraphicsCard([])).toBe(true);
  });
});

describe("export CSV", () => {
  test("le séparateur est le point-virgule, pour Excel en français", () => {
    const csv = timelineCsv([hour()]);
    const [header, row] = csv.split("\n");
    expect(header.split(";")).toHaveLength(7);
    expect(row.split(";")[0]).toBe("2026-05-12T14");
  });

  test("une latence absente donne une cellule vide, pas un zéro", () => {
    const csv = timelineCsv([hour({ latency_ms_average: null, latency_ms_max: null })]);
    const cells = csv.split("\n")[1].split(";");
    expect(cells[5]).toBe("");
    expect(cells[6]).toBe("");
  });

  test("une fenêtre vide garde son en-tête", () => {
    expect(timelineCsv([]).split("\n")).toHaveLength(1);
  });

  test("une adresse contenant un séparateur est protégée", () => {
    const csv = membersCsv([
      {
        email: 'a;b"c@ecole.example',
        transcriptions: 1,
        reformulations: 1,
        audio_seconds: 1,
        latency_ms_average: null,
      },
    ]);
    expect(csv.split("\n")[1]).toStartWith('"a;b""c@ecole.example"');
  });
});

describe("constantes", () => {
  test("l'objectif affiché est celui annoncé", () => {
    expect(LATENCY_OBJECTIVE_MS).toBe(5000);
  });

  test("les fenêtres proposées ne dépassent pas la borne du serveur", () => {
    expect(Math.max(...WINDOWS)).toBe(24 * 31);
  });
});
