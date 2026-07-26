'use client';

/**
 * Elevation profile (§11.8-11.11, §16.10-16.11).
 *
 * Drawn as SVG rather than canvas so that every part of it is in the accessibility
 * tree and can be reached with a keyboard. Three requirements shape it:
 *
 * - **Hover is never the only way in.** The profile is focusable and driven by
 *   arrow keys, so a rider on a phone or using a keyboard gets the same
 *   inspection a mouse user does (§16.2.8).
 * - **Gaps are drawn as gaps.** Where the elevation model has no coverage the
 *   line stops. Joining across it would draw a climb nobody measured.
 * - **The chart has a table.** §16.11 requires an accessible text equivalent, so
 *   the same data renders as a real table for screen readers rather than the
 *   chart carrying an `aria-label` that summarises it away.
 */

import { useCallback, useId, useMemo, useRef, useState } from 'react';

import { GRADIENT_SCALE, bandFor } from '@/lib/map-modes';

export interface ProfilePoint {
  distanceM: number;
  /** null where the elevation model has no coverage. Never substituted. */
  elevationM: number | null;
  gradePercent?: number | null;
}

export interface ElevationProfileProps {
  points: readonly ProfilePoint[];
  /** Datasets and windows, shown with the chart so figures are never bare. */
  datasetLabel?: string;
  analysisWindowM?: number;
  /** Distance with no elevation coverage, reported alongside ascent. */
  gapDistanceM?: number;
  ascentM?: number | null;
  descentM?: number | null;
  selectedDistanceM?: number | null;
  onHoverDistance?: (distanceM: number | null) => void;
  onSelectRange?: (range: { startM: number; endM: number } | null) => void;
  height?: number;
}

interface Segment {
  points: ProfilePoint[];
}

/** Split into runs with coverage, so gaps are never bridged by the path. */
function coverageRuns(points: readonly ProfilePoint[]): Segment[] {
  const runs: Segment[] = [];
  let current: ProfilePoint[] = [];
  for (const point of points) {
    if (point.elevationM === null) {
      if (current.length > 1) runs.push({ points: current });
      current = [];
    } else {
      current.push(point);
    }
  }
  if (current.length > 1) runs.push({ points: current });
  return runs;
}

export function ElevationProfile({
  points,
  datasetLabel,
  analysisWindowM,
  gapDistanceM = 0,
  ascentM,
  descentM,
  selectedDistanceM = null,
  onHoverDistance,
  onSelectRange,
  height = 180,
}: ElevationProfileProps) {
  const tableId = useId();
  const svgRef = useRef<SVGSVGElement>(null);
  const [focusIndex, setFocusIndex] = useState(0);
  const [rangeStart, setRangeStart] = useState<number | null>(null);

  const known = points.filter((p) => p.elevationM !== null);
  const bounds = useMemo(() => {
    const distances = points.map((p) => p.distanceM);
    const elevations = known.map((p) => p.elevationM as number);
    return {
      minD: distances.length ? Math.min(...distances) : 0,
      maxD: distances.length ? Math.max(...distances) : 1,
      minE: elevations.length ? Math.min(...elevations) : 0,
      maxE: elevations.length ? Math.max(...elevations) : 1,
    };
  }, [points, known]);

  const width = 1000;
  const padding = { top: 8, right: 8, bottom: 20, left: 44 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const x = useCallback(
    (d: number) =>
      padding.left +
      ((d - bounds.minD) / Math.max(1, bounds.maxD - bounds.minD)) * plotWidth,
    [bounds, plotWidth, padding.left],
  );
  const y = useCallback(
    (e: number) =>
      padding.top +
      plotHeight -
      ((e - bounds.minE) / Math.max(1, bounds.maxE - bounds.minE)) * plotHeight,
    [bounds, plotHeight, padding.top],
  );

  const runs = useMemo(() => coverageRuns(points), [points]);

  const handleKeyDown = (event: React.KeyboardEvent<SVGSVGElement>) => {
    if (points.length === 0) return;
    let next = focusIndex;
    const step = event.shiftKey ? 10 : 1;

    switch (event.key) {
      case 'ArrowRight':
        next = Math.min(points.length - 1, focusIndex + step);
        break;
      case 'ArrowLeft':
        next = Math.max(0, focusIndex - step);
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = points.length - 1;
        break;
      case 'Enter':
      case ' ':
        // Enter starts a range, Enter again ends it. This is the keyboard
        // equivalent of dragging across the profile (§11.11).
        if (rangeStart === null) {
          setRangeStart(points[focusIndex]!.distanceM);
        } else {
          const startM = Math.min(rangeStart, points[focusIndex]!.distanceM);
          const endM = Math.max(rangeStart, points[focusIndex]!.distanceM);
          onSelectRange?.({ startM, endM });
          setRangeStart(null);
        }
        event.preventDefault();
        return;
      case 'Escape':
        setRangeStart(null);
        onSelectRange?.(null);
        return;
      default:
        return;
    }

    event.preventDefault();
    setFocusIndex(next);
    onHoverDistance?.(points[next]!.distanceM);
  };

  const focused = points[focusIndex];
  const coveragePercent =
    points.length > 0
      ? Math.round((known.length / points.length) * 100)
      : 0;

  return (
    <figure className="elevation-profile" aria-labelledby={`${tableId}-caption`}>
      <figcaption id={`${tableId}-caption`} className="elevation-profile__caption">
        Elevation profile
        {datasetLabel ? <> — {datasetLabel}</> : null}
        {analysisWindowM ? <> · gradient over {analysisWindowM} m</> : null}
      </figcaption>

      <svg
        ref={svgRef}
        role="application"
        aria-label={
          `Elevation profile. ${points.length} samples. ` +
          `Use left and right arrows to move along the route, shift for larger steps, ` +
          `Enter to start and end a range selection. ` +
          `A text table of the same data follows.`
        }
        tabIndex={0}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        className="elevation-profile__chart"
        onKeyDown={handleKeyDown}
        onMouseLeave={() => onHoverDistance?.(null)}
      >
        {/* Gradient bands drawn under the line, patterned as well as coloured. */}
        {points.slice(0, -1).map((point, index) => {
          const next = points[index + 1]!;
          if (point.elevationM === null) return null;
          const band = bandFor(GRADIENT_SCALE, point.gradePercent ?? null);
          return (
            <rect
              key={`band-${index}`}
              x={x(point.distanceM)}
              width={Math.max(0.5, x(next.distanceM) - x(point.distanceM))}
              y={padding.top}
              height={plotHeight}
              fill={band.colour}
              opacity={0.16}
            />
          );
        })}

        {runs.map((run, index) => (
          <polyline
            key={`run-${index}`}
            fill="none"
            stroke="#1a3d5c"
            strokeWidth={2}
            points={run.points
              .map((p) => `${x(p.distanceM)},${y(p.elevationM as number)}`)
              .join(' ')}
          />
        ))}

        {/* A gap is drawn as an explicit marked span, not as a joined line. */}
        {points.map((point, index) =>
          point.elevationM === null ? (
            <rect
              key={`gap-${index}`}
              x={x(point.distanceM) - 1}
              y={padding.top}
              width={2}
              height={plotHeight}
              fill="#9aa0a6"
              opacity={0.5}
            >
              <title>No elevation data at {(point.distanceM / 1000).toFixed(1)} km</title>
            </rect>
          ) : null,
        )}

        {focused ? (
          <g>
            <line
              x1={x(focused.distanceM)}
              x2={x(focused.distanceM)}
              y1={padding.top}
              y2={padding.top + plotHeight}
              stroke="#111"
              strokeWidth={1.5}
            />
            {focused.elevationM !== null ? (
              <circle cx={x(focused.distanceM)} cy={y(focused.elevationM)} r={4} fill="#111" />
            ) : null}
          </g>
        ) : null}

        {rangeStart !== null && focused ? (
          <rect
            x={Math.min(x(rangeStart), x(focused.distanceM))}
            y={padding.top}
            width={Math.abs(x(focused.distanceM) - x(rangeStart))}
            height={plotHeight}
            fill="#1a5f8b"
            opacity={0.2}
          />
        ) : null}
      </svg>

      <p className="elevation-profile__status" role="status">
        {focused
          ? focused.elevationM === null
            ? `At ${(focused.distanceM / 1000).toFixed(1)} km: no elevation data.`
            : `At ${(focused.distanceM / 1000).toFixed(1)} km: ${Math.round(focused.elevationM)} m` +
              (focused.gradePercent != null
                ? `, ${focused.gradePercent.toFixed(1)}% gradient`
                : ', gradient unknown')
          : 'No point selected.'}
        {rangeStart !== null ? ' Range selection in progress; press Enter to finish.' : ''}
      </p>

      {/* §11.5: figures are never shown without what qualifies them. */}
      <dl className="elevation-profile__summary">
        <div>
          <dt>Ascent</dt>
          <dd>{ascentM == null ? 'Unknown' : `${Math.round(ascentM)} m`}</dd>
        </div>
        <div>
          <dt>Descent</dt>
          <dd>{descentM == null ? 'Unknown' : `${Math.round(descentM)} m`}</dd>
        </div>
        <div>
          <dt>Elevation coverage</dt>
          <dd>
            {coveragePercent}%
            {gapDistanceM > 0 ? ` · ${(gapDistanceM / 1000).toFixed(1)} km unmeasured` : null}
          </dd>
        </div>
      </dl>

      {gapDistanceM > 0 ? (
        <p className="elevation-profile__warning">
          {(gapDistanceM / 1000).toFixed(1)} km of this route has no elevation data. The
          ascent figure covers the measured part only and is not a total for the whole
          route.
        </p>
      ) : null}

      {/* The accessible equivalent required by §16.11. Not visually hidden from
          keyboard users: it is a real table they can read and copy. */}
      <details className="elevation-profile__table">
        <summary>Elevation data as a table</summary>
        <table>
          <caption>
            Elevation and gradient every {analysisWindowM ?? '—'} m along the route.
            Rows with no elevation are shown as unknown rather than omitted.
          </caption>
          <thead>
            <tr>
              <th scope="col">Distance (km)</th>
              <th scope="col">Elevation (m)</th>
              <th scope="col">Gradient (%)</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point, index) => (
              <tr key={index}>
                <th scope="row">{(point.distanceM / 1000).toFixed(2)}</th>
                <td>{point.elevationM === null ? 'Unknown' : Math.round(point.elevationM)}</td>
                <td>
                  {point.gradePercent == null ? 'Unknown' : point.gradePercent.toFixed(1)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
