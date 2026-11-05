import { describe, expect, test } from "bun:test";

import { subscriptionAlerts, type SubscriptionReport } from "./subscription";

const REPORT: SubscriptionReport = {
  licence: { status: "valid", degraded: false, tier: "151-250", ends: "2027-08-31" },
  active_month: 120,
  active_year: 180,
  year_from: "2026-09-01",
  seats: 250,
  near_limit: false,
  over_limit: false,
  renewal_reminder: false,
  ai_envelope: null,
};

const ENVELOPE = {
  annual_eur: 100,
  allowance_eur: 30,
  spent_eur: 25,
  month_eur: 4,
  percent: 83,
  empty: false,
};

describe("subscriptionAlerts", () => {
  test("rien à signaler", () => {
    expect(subscriptionAlerts(REPORT)).toEqual([]);
  });

  test("l'enveloppe d'IA alerte à 80 % puis vide", () => {
    expect(subscriptionAlerts({ ...REPORT, ai_envelope: ENVELOPE })).toEqual(["envelopeNear"]);
    expect(
      subscriptionAlerts({ ...REPORT, ai_envelope: { ...ENVELOPE, percent: 100, empty: true } }),
    ).toEqual(["envelopeEmpty"]);
  });

  test("l'échéance passe avant la jauge", () => {
    expect(
      subscriptionAlerts({
        ...REPORT,
        licence: { ...REPORT.licence, status: "grace" },
        near_limit: true,
        over_limit: true,
      }),
    ).toEqual(["grace", "overLimit"]);
  });

  test("approche du palier", () => {
    expect(subscriptionAlerts({ ...REPORT, near_limit: true })).toEqual(["nearLimit"]);
  });
});
