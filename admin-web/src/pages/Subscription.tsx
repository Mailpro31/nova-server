/**
 * Abonnement (2.1) : le palier souscrit, les dates, et les personnes actives
 * face au palier. Des comptes, jamais de noms ; rien ne bloque personne.
 */

import { AsyncSection, Facts, Notice, Panel } from "../components/primitives";
import { t } from "../lib/messages";
import { subscriptionAlerts, type SubscriptionReport } from "../lib/subscription";
import { useSession } from "../lib/useAdminSession";
import { useResource } from "../lib/useResource";

export function alertText(alert: ReturnType<typeof subscriptionAlerts>[number], report: SubscriptionReport) {
  const days = report.licence.days_left ?? 0;
  const grace = (report.licence.grace_days ?? 0) + days;
  switch (alert) {
    case "warning":
      return t.subscription.alerts.warning(days);
    case "grace":
      return t.subscription.alerts.grace(grace);
    default:
      return t.subscription.alerts[alert];
  }
}

const euros = (value: number) =>
  value.toLocaleString(undefined, { style: "currency", currency: "EUR" });

export function Subscription() {
  const { control } = useSession();
  const [state, retry] = useResource<SubscriptionReport>(
    control ? () => control.subscription() : null,
    [],
  );

  return (
    <Panel title={t.subscription.title}>
      <p className="panel-intro">{t.subscription.description}</p>
      <AsyncSection state={state} onRetry={retry}>
        {(report) => {
          const { licence } = report;
          return (
            <>
              {subscriptionAlerts(report).map((alert) => (
                <Notice key={alert} tone={alert === "expired" ? "danger" : undefined}>
                  {alertText(alert, report)}
                </Notice>
              ))}
              {licence.status === "none" && <Notice>{t.subscription.none}</Notice>}
              {licence.status !== "none" && licence.status !== "invalid" && (
                <Facts
                  entries={[
                    [t.subscription.kind, licence.kind ? t.subscription.kinds[licence.kind] : "—"],
                    [t.subscription.tier, licence.tier ?? "—"],
                    [
                      t.subscription.period,
                      t.subscription.periodValue(licence.starts ?? "—", licence.ends ?? "—"),
                    ],
                    [t.subscription.daysLeft, String(Math.max(0, licence.days_left ?? 0))],
                  ]}
                />
              )}
              <Facts
                entries={[
                  [t.subscription.activeMonth, String(report.active_month)],
                  [t.subscription.activeYear(report.year_from), String(report.active_year)],
                ]}
              />
              {report.ai_envelope && (
                <>
                  <Facts
                    entries={[
                      [t.subscription.envelopeAnnual, euros(report.ai_envelope.annual_eur)],
                      [t.subscription.envelopeOpen, euros(report.ai_envelope.allowance_eur)],
                      [t.subscription.envelopeSpent, euros(report.ai_envelope.spent_eur)],
                      [t.subscription.envelopeMonth, euros(report.ai_envelope.month_eur)],
                    ]}
                  />
                  <meter
                    min={0}
                    max={100}
                    high={80}
                    value={Math.min(report.ai_envelope.percent, 100)}
                    aria-label={t.subscription.envelopeGauge(report.ai_envelope.percent)}
                    style={{ width: "100%" }}
                  />
                  <p>{t.subscription.envelopeGauge(report.ai_envelope.percent)}</p>
                </>
              )}
              {report.seats !== null && (
                <>
                  <meter
                    min={0}
                    max={report.seats}
                    high={report.seats * 0.9}
                    value={Math.min(report.active_month, report.seats)}
                    aria-label={t.subscription.gauge(report.active_month, report.seats)}
                    style={{ width: "100%" }}
                  />
                  <p>{t.subscription.gauge(report.active_month, report.seats)}</p>
                </>
              )}
            </>
          );
        }}
      </AsyncSection>
    </Panel>
  );
}
