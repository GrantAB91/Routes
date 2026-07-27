'use client';

/**
 * The planner (§3.1, §3.5, §16.1).
 *
 * The map is the working surface and the panel beside it is compact. Route
 * creation is not buried in a modal: the request field, the interpreted intent
 * and the mode switch are all on this screen.
 *
 * §3.1's shortest path — describe, review, inspect, stage, export — is the order
 * of the panel top to bottom.
 */

import { useCallback, useMemo, useState } from 'react';

import { ElevationProfile, type ProfilePoint } from '@/components/ElevationProfile';
import { MapLegend } from '@/components/MapLegend';
import { RouteMap, type MapSegment } from '@/components/RouteMap';
import { RouteSummary, type Verdict } from '@/components/RouteSummary';
import { RouteTopology, type TopologyNode } from '@/components/RouteTopology';
import { ApiError, apiFetch } from '@/lib/api';
import { COLOUR_SCALES, MAP_MODES, type MapModeId, mapMode } from '@/lib/map-modes';
import { basemapStateFromEnv } from '@/lib/map-provider';
import { generateRoute, type GeneratedRoute, type Waypoint } from '@/lib/route';

interface ParsedField {
  value: unknown;
  phrase: string;
  note: string;
}

interface ParseResponse {
  parser: string;
  parser_version: string;
  fields: Record<string, ParsedField>;
  place_mentions: Record<string, string>;
  unresolved: string[];
  blocking: string[];
  confirmed: boolean;
}

const FIELD_LABELS: Record<string, string> = {
  bicycle_type: 'Bicycle',
  max_daily_distance_m: 'Maximum distance per day',
  max_daily_ascent_m: 'Maximum ascent per day',
  max_total_distance_m: 'Maximum total distance',
  max_gradient_percent: 'Maximum gradient',
  max_detour_ratio: 'Maximum detour',
  number_of_days: 'Number of days',
  avoid_hills: 'Minimise ascent',
  coast_preference: 'Follow the coast',
  corridor_preference: 'Stay near the official route',
  reduce_road_exposure: 'Reduce traffic exposure',
  prefer_cycle_infrastructure: 'Prefer cycle infrastructure',
  require_legal_access: 'Use legal cycling roads',
  prefer_gravel: 'Favour unsealed surfaces',
  prefer_paved: 'Prefer paved surfaces',
  require_known_surface: 'Require recorded surface data',
  ferry_preference: 'Ferries',
  named_route: 'Named route',
};

function formatValue(key: string, value: unknown): string {
  if (value === true) return 'Yes';
  if (value === false) return 'No';
  if (typeof value === 'number') {
    if (key.endsWith('_m')) return `${(value / 1000).toFixed(1)} km`;
    if (key.endsWith('_percent')) return `${value}%`;
    if (key.endsWith('_ratio')) return `${Math.round((value - 1) * 100)}% longer`;
    return String(value);
  }
  return String(value);
}

export default function PlannerPage() {
  const basemap = useMemo(() => basemapStateFromEnv(), []);
  const [request, setRequest] = useState('');
  const [parsed, setParsed] = useState<ParseResponse | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [modeId, setModeId] = useState<MapModeId>('standard');
  const [hoverDistanceM, setHoverDistanceM] = useState<number | null>(null);
  const [selectedDistanceM, setSelectedDistanceM] = useState<number | null>(null);

  const mode = mapMode(modeId);
  const scale = mode.colourMode ? COLOUR_SCALES[mode.colourMode] : null;

  const [route, setRoute] = useState<GeneratedRoute | null>(null);
  const [waypoints, setWaypoints] = useState<Waypoint[]>([]);
  const [routing, setRouting] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  // Empty until a route exists. The chart and the map each say so in their own
  // words rather than rendering an empty frame that reads as a failure.
  const profilePoints: ProfilePoint[] = route?.profilePoints ?? [];
  const topologyNodes: TopologyNode[] = route?.topologyNodes ?? [];
  const segments: MapSegment[] = route?.segments ?? [];

  const parse = useCallback(async () => {
    setBusy(true);
    setParseError(null);
    try {
      const response = await apiFetch<ParseResponse>('/v1/intent/parse', {
        method: 'POST',
        body: JSON.stringify({ text: request }),
      });
      setParsed(response);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setParseError(
          caught.remedy ? `${caught.message} ${caught.remedy}` : caught.message,
        );
      } else {
        setParseError('The request could not be read.');
      }
    } finally {
      setBusy(false);
    }
  }, [request]);

  const addWaypoint = useCallback((point: { lat: number; lon: number }) => {
    setWaypoints((current) => [...current, { ...point, kind: 'break' as const }]);
  }, []);

  const moveWaypoint = useCallback((index: number, point: { lat: number; lon: number }) => {
    setWaypoints((current) =>
      current.map((existing, at) => (at === index ? { ...existing, ...point } : existing)),
    );
  }, []);

  const removeWaypoint = useCallback((index: number) => {
    setWaypoints((current) => current.filter((_, at) => at !== index));
  }, []);

  const generate = useCallback(async () => {
    setRouting(true);
    setRouteError(null);
    try {
      setRoute(await generateRoute(waypoints));
    } catch (caught) {
      if (caught instanceof ApiError) {
        setRouteError(caught.remedy ? `${caught.message} ${caught.remedy}` : caught.message);
      } else {
        setRouteError('The route could not be generated.');
      }
      setRoute(null);
    } finally {
      setRouting(false);
    }
  }, [waypoints]);

  return (
    <div className="planner">
      <div className="panel">
        <h1>Plan a route</h1>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void parse();
          }}
        >
          <label htmlFor="request">Describe the journey</label>
          <textarea
            id="request"
            name="request"
            rows={3}
            value={request}
            onChange={(event) => setRequest(event.target.value)}
            placeholder="Plan the Wild Atlantic Way for a road bike, keep each day below 100 km and 1400 m of ascent."
            style={{ width: '100%', font: 'inherit', padding: '0.5rem' }}
          />
          <button type="submit" disabled={busy || request.trim().length === 0}>
            {busy ? 'Reading…' : 'Read my request'}
          </button>
        </form>

        {parseError ? (
          <p role="alert" style={{ color: 'var(--danger)' }}>
            {parseError}
          </p>
        ) : null}

        {parsed ? (
          <section aria-labelledby="intent-heading">
            <h2 id="intent-heading">What Contour understood</h2>
            <p>
              Check this before routing. Every value shows the words it came from, and
              you can change any of them.
            </p>

            <table>
              <caption>
                Interpreted by the {parsed.parser} parser, version{' '}
                {parsed.parser_version}.
              </caption>
              <thead>
                <tr>
                  <th scope="col">Setting</th>
                  <th scope="col">Value</th>
                  <th scope="col">From your words</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(parsed.fields).map(([key, field]) => (
                  <tr key={key}>
                    <th scope="row">{FIELD_LABELS[key] ?? key.replace(/_/g, ' ')}</th>
                    <td>
                      {formatValue(key, field.value)}
                      {field.note ? (
                        <>
                          <br />
                          <small>{field.note}</small>
                        </>
                      ) : null}
                    </td>
                    <td>
                      <q>{field.phrase}</q>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {Object.keys(parsed.place_mentions).length > 0 ? (
              <p>
                Places named: {Object.values(parsed.place_mentions).join(' to ')}. These
                have not been turned into coordinates — Contour does not guess a
                location, and no geocoder is configured.
              </p>
            ) : null}

            {parsed.blocking.length > 0 ? (
              <p role="alert">
                Contour needs a start and finish, or a named route, before it can plan
                anything.
              </p>
            ) : null}
          </section>
        ) : null}

        <section aria-labelledby="waypoints-heading">
          <h2 id="waypoints-heading">Points on the route</h2>
          <p>
            No geocoder is configured, so places are not turned into coordinates.
            Click the map to place a start, a finish and any points to pass through.
          </p>

          {waypoints.length === 0 ? (
            <p>Nothing placed yet.</p>
          ) : (
            <ol className="waypoint-list">
              {waypoints.map((point, index) => (
                <li key={`${point.lat},${point.lon},${index}`}>
                  <span>
                    {index === 0
                      ? 'Start'
                      : index === waypoints.length - 1
                        ? 'Finish'
                        : `Via ${index}`}
                    : {point.lat.toFixed(4)}, {point.lon.toFixed(4)}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeWaypoint(index)}
                    aria-label={`Remove point ${index + 1}`}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ol>
          )}

          <button
            type="button"
            onClick={() => void generate()}
            disabled={routing || waypoints.length < 2}
          >
            {routing ? 'Routing…' : 'Generate route'}
          </button>
          {waypoints.length < 2 ? (
            <p>
              <small>A route needs at least a start and a finish.</small>
            </p>
          ) : null}

          {routeError ? (
            <p role="alert" style={{ color: 'var(--danger)' }}>
              {routeError}
            </p>
          ) : null}
        </section>

        {route ? (
          <>
            <RouteSummary
              name="Generated route"
              originKind="generated"
              distanceM={route.distanceM}
              ascentM={route.ascentM}
              descentM={route.descentM}
              maxGradePercent={route.maxGradePercent}
              gradientWindowM={route.gradientWindowM}
              surfaceCompositionM={route.surfaceCompositionM}
              verdict={route.validation.verdict as Verdict}
              violations={route.validation.violations.map((violation) => ({
                constraintKey: violation.constraint_key,
                statedAs: violation.stated_as,
                requested: violation.requested as number | string | null,
                observed: violation.observed as number | string | null,
                distanceM: violation.distance_m,
                detail: violation.detail,
              }))}
              unevaluated={route.validation.unevaluated}
              standingLimitations={route.validation.unavailable_checks}
              sources={route.sources}
            />
            {route.elevationNote ? <p role="note">{route.elevationNote}</p> : null}
          </>
        ) : null}
      </div>

      <div>
        <div className="mode-switch" role="group" aria-label="Map mode">
          {MAP_MODES.map((candidate) => (
            <button
              key={candidate.id}
              type="button"
              aria-pressed={candidate.id === modeId}
              onClick={() => setModeId(candidate.id)}
            >
              {candidate.label}
            </button>
          ))}
        </div>

        <p className="visually-hidden" role="status">
          {mode.label} mode. {mode.description}
        </p>

        <div className="map-shell">
          {!basemap.available && mode.requiresBasemap ? (
            <p className="map-notice">
              <strong>No basemap is configured.</strong> {basemap.reason} {basemap.remedy}
            </p>
          ) : null}

          {mode.requiresTerrain && !basemap.terrainUrl ? (
            <p className="map-notice">
              <strong>No terrain data is configured.</strong> The 3D view needs
              elevation tiles, which are a separate product from the basemap. The
              route and its measured elevation profile are unaffected.
            </p>
          ) : null}

          {mode.isSchematic ? (
            <RouteTopology nodes={topologyNodes} />
          ) : (
            <RouteMap
              segments={segments}
              mode={mode}
              scale={scale}
              basemap={basemap}
              hoverDistanceM={hoverDistanceM}
              waypoints={waypoints}
              onAddWaypoint={addWaypoint}
              onMoveWaypoint={moveWaypoint}
              onRemoveWaypoint={removeWaypoint}
              onSelectSegment={(segment) =>
                setSelectedDistanceM(segment ? segment.startDistanceM : null)
              }
            />
          )}
        </div>

        {scale ? <MapLegend scale={scale} /> : null}

        <ElevationProfile
          points={profilePoints}
          gapDistanceM={route?.elevationGapM ?? 0}
          ascentM={route?.ascentM ?? null}
          descentM={route?.descentM ?? null}
          selectedDistanceM={selectedDistanceM}
          onHoverDistance={setHoverDistanceM}
        />
        <p className="visually-hidden" role="status">
          {hoverDistanceM === null
            ? 'No point selected on the elevation profile.'
            : `Elevation profile at ${(hoverDistanceM / 1000).toFixed(1)} kilometres.`}
        </p>
      </div>
    </div>
  );
}
