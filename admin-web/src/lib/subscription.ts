/**
 * L'abonnement de l'école (2.1) : la licence signée, et les personnes actives
 * face au palier. Des comptes, jamais de noms ; rien ne bloque personne.
 */

export type LicenceStatus = "none" | "invalid" | "valid" | "warning" | "grace" | "expired";

export interface Licence {
  status: LicenceStatus;
  degraded: boolean;
  organization?: string;
  tier?: string;
  kind?: "pilot" | "subscription";
  starts?: string;
  ends?: string;
  days_left?: number;
  grace_days?: number;
}

/** L'enveloppe d'IA du contrat (2.3), en euros ; `null` sans enveloppe ou sans prix. */
export interface AiEnvelope {
  annual_eur: number;
  allowance_eur: number;
  spent_eur: number;
  month_eur: number;
  percent: number;
  empty: boolean;
}

export interface SubscriptionReport {
  licence: Licence;
  active_month: number;
  active_year: number;
  year_from: string;
  seats: number | null;
  near_limit: boolean;
  over_limit: boolean;
  renewal_reminder: boolean;
  ai_envelope: AiEnvelope | null;
}

/** Ce que la console doit signaler, le plus pressant d'abord. */
export function subscriptionAlerts(
  report: SubscriptionReport,
): (
  | "expired"
  | "grace"
  | "warning"
  | "invalid"
  | "envelopeEmpty"
  | "envelopeNear"
  | "overLimit"
  | "nearLimit"
)[] {
  const alerts: ReturnType<typeof subscriptionAlerts> = [];
  const status = report.licence.status;
  if (status === "expired" || status === "grace" || status === "warning" || status === "invalid")
    alerts.push(status);
  if (report.ai_envelope?.empty) alerts.push("envelopeEmpty");
  else if ((report.ai_envelope?.percent ?? 0) >= 80) alerts.push("envelopeNear");
  if (report.over_limit) alerts.push("overLimit");
  else if (report.near_limit) alerts.push("nearLimit");
  return alerts;
}
