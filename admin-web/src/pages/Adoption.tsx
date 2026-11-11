/**
 * Adoption : ce qu'une direction ou une mission handicap reprend dans un bilan.
 *
 * Combien de personnes se servent de Nova chaque semaine, combien y
 * reviennent, combien elles dictent. Des comptes, jamais de noms : la page
 * est lisible par un rôle en lecture seule, et rien n'est inscrit au journal.
 *
 * Des tableaux plutôt que des courbes : un bilan reprend des chiffres, et un
 * tableau se lit au lecteur d'écran.
 */

import { AsyncSection, Facts, Notice, Panel } from "../components/primitives";
import {
  adoptionCsv,
  latestWeek,
  monthLabel,
  weekLabel,
  type AdoptionCounts,
  type AdoptionReport,
} from "../lib/adoption";
import { t } from "../lib/messages";
import { useSession } from "../lib/useAdminSession";
import { useResource } from "../lib/useResource";

function download(name: string, content: string) {
  const blob = new Blob([`﻿${content}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(value);
}

function CountCells({
  counts,
  minPeople,
  regular,
}: {
  counts: AdoptionCounts;
  minPeople: number;
  regular: boolean;
}) {
  if (!counts.measured) {
    return <td colSpan={regular ? 4 : 3}>{t.adoption.notMeasured}</td>;
  }
  if (counts.masked) {
    return <td colSpan={regular ? 4 : 3}>{t.adoption.masked(minPeople)}</td>;
  }
  return (
    <>
      <td>{count(counts.people)}</td>
      {regular && <td>{count(counts.regular_people)}</td>}
      <td>{count(counts.dictations)}</td>
      <td>{count(counts.audio_minutes)}</td>
    </>
  );
}

export function Adoption() {
  const { control } = useSession();
  const [state, retry] = useResource<AdoptionReport>(
    control ? () => control.adoption() : null,
    [],
  );

  return (
    <Panel title={t.adoption.title}>
      <p className="panel-intro">{t.adoption.description}</p>
      <AsyncSection state={state} onRetry={retry}>
        {(report) => {
          const latest = latestWeek(report);
          return (
            <>
              {latest ? (
                <Facts
                  entries={[
                    [t.adoption.latestWeek, weekLabel(latest.week_start)],
                    [t.adoption.people, count(latest.people)],
                    [t.adoption.regularPeople(report.regular_days), count(latest.regular_people)],
                    [t.adoption.dictations, count(latest.dictations)],
                  ]}
                />
              ) : (
                <Notice>{t.adoption.empty}</Notice>
              )}

              <Notice>{t.adoption.maskRule(report.min_people)}</Notice>
              <Notice>
                {report.measured_from
                  ? t.adoption.measuredFrom(report.measured_from)
                  : t.adoption.notMeasuredYet}
              </Notice>

              <h4>{t.adoption.byWeek}</h4>
              <table className="usage-members" aria-label={t.adoption.byWeek}>
                <thead>
                  <tr>
                    <th scope="col">{t.adoption.week}</th>
                    <th scope="col">{t.adoption.people}</th>
                    <th scope="col">{t.adoption.regularPeople(report.regular_days)}</th>
                    <th scope="col">{t.adoption.dictations}</th>
                    <th scope="col">{t.adoption.minutes}</th>
                  </tr>
                </thead>
                <tbody>
                  {[...report.weeks].reverse().map((week) => (
                    <tr key={week.week_start}>
                      <td>
                        {weekLabel(week.week_start)}
                        {week.partial && ` ${t.adoption.partial}`}
                      </td>
                      <CountCells counts={week} minPeople={report.min_people} regular />
                    </tr>
                  ))}
                </tbody>
              </table>

              <h4>{t.adoption.byMonth}</h4>
              {report.months.length === 0 ? (
                <Notice>{t.adoption.empty}</Notice>
              ) : (
                <table className="usage-members" aria-label={t.adoption.byMonth}>
                  <thead>
                    <tr>
                      <th scope="col">{t.adoption.month}</th>
                      <th scope="col">{t.adoption.people}</th>
                      <th scope="col">{t.adoption.dictations}</th>
                      <th scope="col">{t.adoption.minutes}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...report.months].reverse().map((month) => (
                      <tr key={month.month}>
                        <td>
                          {monthLabel(month.month)}
                          {month.partial && ` ${t.adoption.partial}`}
                        </td>
                        <CountCells
                          counts={month}
                          minPeople={report.min_people}
                          regular={false}
                        />
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <h4>{t.adoption.retentionTitle}</h4>
              <Notice>
                {report.retention.masked
                  ? t.adoption.masked(report.min_people)
                  : t.adoption.retention(
                      report.retention.continuing ?? 0,
                      report.retention.started ?? 0,
                      report.retention.days,
                    )}
              </Notice>

              <h4>{t.adoption.surveyTitle}</h4>
              <p className="panel-intro">{t.adoption.surveyLead(report.min_people)}</p>
              {report.survey.length === 0 ? (
                <Notice>{t.adoption.noSurvey}</Notice>
              ) : (
                <table className="usage-members" aria-label={t.adoption.surveyTitle}>
                  <thead>
                    <tr>
                      <th scope="col">{t.adoption.month}</th>
                      <th scope="col">{t.adoption.surveyAverage}</th>
                      <th scope="col">{t.adoption.surveyAnswers}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.survey.map((entry) =>
                      entry.average === null ? (
                        <tr key={entry.month}>
                          <td>{monthLabel(entry.month)}</td>
                          <td colSpan={2}>{t.adoption.masked(report.min_people)}</td>
                        </tr>
                      ) : (
                        <tr key={entry.month}>
                          <td>{monthLabel(entry.month)}</td>
                          <td>{entry.average} / 5</td>
                          <td>{count(entry.answers)}</td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              )}

              <h4>{t.adoption.versions}</h4>
              <p className="panel-intro">{t.adoption.versionsLead(report.version_days)}</p>
              {report.versions.length === 0 ? (
                <Notice>{t.adoption.noVersions}</Notice>
              ) : (
                <table className="usage-members" aria-label={t.adoption.versions}>
                  <thead>
                    <tr>
                      <th scope="col">{t.adoption.version}</th>
                      <th scope="col">{t.adoption.people}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.versions.map((entry) => (
                      <tr key={entry.version}>
                        <td>{entry.version}</td>
                        <td>{entry.people}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="usage-actions">
                <button
                  type="button"
                  onClick={() => download("nova-adoption.csv", adoptionCsv(report))}
                >
                  {t.adoption.exportCsv}
                </button>
                {/* Le PDF passe par l'impression du navigateur : rien à installer. */}
                <button type="button" onClick={() => window.print()}>
                  {t.adoption.exportPdf}
                </button>
              </div>
            </>
          );
        }}
      </AsyncSection>
    </Panel>
  );
}
