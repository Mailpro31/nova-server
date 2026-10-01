/**
 * Tableau de bord d'usage.
 *
 * Ce que l'établissement veut savoir : combien de dictées, par combien de
 * personnes, à quelle heure, en combien de temps, et ce que la machine encaisse
 * pendant ce temps.
 *
 * ## Pourquoi le pire cas est affiché à côté de la moyenne
 *
 * Une moyenne de deux secondes peut cacher une dictée à douze, et c'est
 * celle-là qui fait douter de l'outil. Le résumé compte donc aussi les heures
 * où le pire cas a dépassé l'objectif : un dépassement ne condamne rien, dix
 * heures sur vingt-quatre, si.
 *
 * ## Pourquoi le détail nominatif est derrière un bouton
 *
 * Il nomme des salariés, le serveur ne le rend qu'à un rôle qui gère les
 * personnes, et chaque lecture est inscrite au journal. L'afficher d'office
 * remplirait le journal à chaque rafraîchissement, et exposerait par défaut ce
 * qui doit rester un geste délibéré.
 */

import { useState } from "react";
import { AsyncSection, Facts, Notice, Panel, Tag } from "../components/primitives";
import { t } from "../lib/messages";
import { CAPABILITY, can } from "../lib/session";
import { useSession } from "../lib/useAdminSession";
import { useResource } from "../lib/useResource";
import {
  LATENCY_OBJECTIVE_MS,
  PLOT,
  WINDOWS,
  axisLabels,
  axisMaximum,
  bars,
  linePath,
  membersCsv,
  sampleLabel,
  seconds,
  share,
  timelineCsv,
  withoutGraphicsCard,
  type HostSample,
  type UsageHour,
  type UsageMetrics as Metrics,
} from "../lib/usageMetrics";

function download(name: string, content: string) {
  const blob = new Blob([`﻿${content}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function Chart({
  title,
  labels,
  children,
  legend,
}: {
  title: string;
  labels: string[];
  children: React.ReactNode;
  legend?: React.ReactNode;
}) {
  return (
    <div className="usage-chart">
      <div className="usage-chart__head">
        <h4>{title}</h4>
        {legend}
      </div>
      <svg
        viewBox={`0 0 ${PLOT.width} ${PLOT.height}`}
        className="usage-chart__plot"
        role="img"
        aria-label={title}
        preserveAspectRatio="none"
      >
        {children}
      </svg>
      <div className="usage-chart__axis">
        {labels.map((label, index) => (
          <span key={`${label}-${index}`}>{label}</span>
        ))}
      </div>
    </div>
  );
}

function UsageCharts({ timeline, withDay }: { timeline: readonly UsageHour[]; withDay: boolean }) {
  const labels = axisLabels(timeline.map((h) => h.hour), withDay);
  const dictations = timeline.map((h) => h.transcriptions + h.reformulations);
  const dictationMax = axisMaximum(dictations);
  const peopleMax = axisMaximum(timeline.map((h) => h.people));
  const latencyMax = axisMaximum([
    ...timeline.map((h) => h.latency_ms_max),
    LATENCY_OBJECTIVE_MS,
  ]);
  const objectiveY = PLOT.height - (LATENCY_OBJECTIVE_MS / latencyMax) * PLOT.height;

  return (
    <>
      <Chart title={t.usageMetrics.dictationsByHour} labels={labels}>
        {bars(dictations, dictationMax).map((bar, index) => (
          <rect key={index} {...bar} className="usage-bar" />
        ))}
      </Chart>

      <Chart title={t.usageMetrics.peopleByHour} labels={labels}>
        <path
          d={linePath(timeline.map((h) => h.people), peopleMax)}
          className="usage-line usage-line--people"
        />
      </Chart>

      <Chart
        title={t.usageMetrics.latencyByHour}
        labels={labels}
        legend={<span className="usage-legend">{t.usageMetrics.objectiveLegend}</span>}
      >
        <line
          x1={0}
          x2={PLOT.width}
          y1={objectiveY}
          y2={objectiveY}
          className="usage-objective"
        />
        <path
          d={linePath(timeline.map((h) => h.latency_ms_max), latencyMax)}
          className="usage-line usage-line--peak"
        />
        <path
          d={linePath(timeline.map((h) => h.latency_ms_average), latencyMax)}
          className="usage-line usage-line--average"
        />
      </Chart>
    </>
  );
}

function HostCharts({ host }: { host: readonly HostSample[] }) {
  const labels = host.length
    ? axisLabels(host.map((s) => `${s.sampled_at.split("T")[0]}T00`), false).map(
        (_, index, all) =>
          sampleLabel(host[Math.round((index * (host.length - 1)) / Math.max(1, all.length - 1))].sampled_at),
      )
    : [];
  const noCard = withoutGraphicsCard(host);

  return (
    <>
      <Chart
        title={t.usageMetrics.cpuAndMemory}
        labels={labels}
        legend={<span className="usage-legend">{t.usageMetrics.percentLegend}</span>}
      >
        <path
          d={linePath(host.map((s) => s.cpu_percent), 100)}
          className="usage-line usage-line--cpu"
        />
        <path
          d={linePath(host.map((s) => share(s.ram_used_gb, s.ram_total_gb)), 100)}
          className="usage-line usage-line--ram"
        />
      </Chart>

      {noCard ? (
        <Notice>{t.usageMetrics.noGraphicsCard}</Notice>
      ) : (
        <Chart
          title={t.usageMetrics.graphicsCard}
          labels={labels}
          legend={<span className="usage-legend">{t.usageMetrics.percentLegend}</span>}
        >
          <path
            d={linePath(host.map((s) => s.gpu_percent), 100)}
            className="usage-line usage-line--gpu"
          />
          <path
            d={linePath(host.map((s) => share(s.vram_used_gb, s.vram_total_gb)), 100)}
            className="usage-line usage-line--vram"
          />
        </Chart>
      )}
    </>
  );
}

export function UsageMetrics() {
  const { control, session } = useSession();
  const [hours, setHours] = useState<number>(WINDOWS[0]);
  const [named, setNamed] = useState(false);
  const maySeeNames = can(session, CAPABILITY.identityManage);

  const [state, retry] = useResource<Metrics>(
    control ? () => control.usageMetrics(hours, named && maySeeNames) : null,
    [hours, named, maySeeNames],
  );

  return (
    <Panel title={t.usageMetrics.title}>
      <p className="panel-intro">{t.usageMetrics.description}</p>
      <div className="usage-windows">
        {WINDOWS.map((window) => (
          <button
            key={window}
            type="button"
            className={window === hours ? "is-active" : undefined}
            onClick={() => setHours(window)}
          >
            {t.usageMetrics.window[String(window) as keyof typeof t.usageMetrics.window]}
          </button>
        ))}
      </div>

      <AsyncSection state={state} onRetry={retry}>
        {(metrics) => {
          const withDay = hours > 24;
          const summary = metrics.summary;
          return (
            <>
              <Facts
                entries={[
                  [t.usageMetrics.dictations, String(summary.dictations)],
                  [t.usageMetrics.peoplePeak, String(summary.people_peak)],
                  [
                    t.usageMetrics.busiestHour,
                    summary.busiest_hour
                      ? axisLabels([summary.busiest_hour], withDay)[0]
                      : "—",
                  ],
                  [t.usageMetrics.latencyAverage, seconds(summary.latency_ms_average)],
                  [
                    t.usageMetrics.latencyPeak,
                    <>
                      {seconds(summary.latency_ms_max)}{" "}
                      {summary.over_five_seconds > 0 && (
                        <Tag tone="warn">
                          {t.usageMetrics.hoursOverObjective(summary.over_five_seconds)}
                        </Tag>
                      )}
                    </>,
                  ],
                ]}
              />

              {metrics.timeline.length === 0 ? (
                <Notice>{t.usageMetrics.empty}</Notice>
              ) : (
                <UsageCharts timeline={metrics.timeline} withDay={withDay} />
              )}

              <HostCharts host={metrics.host} />

              <div className="usage-actions">
                <button
                  type="button"
                  onClick={() =>
                    download(`nova-usage-${metrics.since}.csv`, timelineCsv(metrics.timeline))
                  }
                >
                  {t.usageMetrics.exportCsv}
                </button>
                {maySeeNames && !named && (
                  <button type="button" onClick={() => setNamed(true)}>
                    {t.usageMetrics.showNames}
                  </button>
                )}
              </div>

              {named && maySeeNames && (
                <>
                  <Notice>{t.usageMetrics.namesAreJournalled}</Notice>
                  <table className="usage-members">
                    <thead>
                      <tr>
                        <th>{t.usageMetrics.member}</th>
                        <th>{t.usageMetrics.dictations}</th>
                        <th>{t.usageMetrics.latencyAverage}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(metrics.members ?? []).map((member) => (
                        <tr key={member.email}>
                          <td>{member.email}</td>
                          <td>{member.transcriptions + member.reformulations}</td>
                          <td>{seconds(member.latency_ms_average)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="usage-actions">
                    <button
                      type="button"
                      onClick={() =>
                        download(
                          `nova-usage-membres-${metrics.since}.csv`,
                          membersCsv(metrics.members ?? []),
                        )
                      }
                    >
                      {t.usageMetrics.exportMembersCsv}
                    </button>
                  </div>
                </>
              )}
            </>
          );
        }}
      </AsyncSection>
    </Panel>
  );
}
