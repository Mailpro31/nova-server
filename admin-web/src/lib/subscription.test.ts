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
};

describe("subscriptionAlerts", () => {
  test("rien à signaler", () => {
    expect(subscriptionAlerts(REPORT)).toEqual([]);
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
