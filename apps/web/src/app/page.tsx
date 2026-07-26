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
import { RouteTopology, type TopologyNode } from '@/components/RouteTopology';
import { ApiError, apiFetch } from '@/lib/api';
import { COLOUR_SCALES, MAP_MODES, type MapModeId, mapMode } from '@/lib/map-modes';
import { basemapStateFromEnv } from '@/lib/map-provider';

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

  const mode = mapMode(modeId);
  const scale = mode.colourMode ? COLOUR_SCALES[mode.colourMode] : null;

  // No route has been generated yet: routing needs tiles, which need an OSM
  // extract. The panel says so rather than showing an empty chart.
  const profilePoints: ProfilePoint[] = [];
  const topologyNodes: TopologyNode[] = [];

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

          {mode.isSchematic ? (
            <RouteTopology nodes={topologyNodes} />
          ) : (
            <div className="map-canvas" aria-label="Map" role="region">
              <p className="map-notice">
                <strong>No route has been generated yet.</strong> Route generation needs
                routing tiles, which are built from an OpenStreetMap extract. The
                Coverage screen lists what is missing and why.
              </p>
            </div>
          )}
        </div>

        {scale ? <MapLegend scale={scale} /> : null}

        <ElevationProfile
          points={profilePoints}
          gapDistanceM={0}
          ascentM={null}
          descentM={null}
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
