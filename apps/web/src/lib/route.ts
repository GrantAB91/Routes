/**
 * Generating a route and shaping the response for the components (§7, §16.7).
 *
 * The API returns three-state attributes and a four-state verdict, and this
 * module carries both through unchanged. Nothing here collapses `unknown` into
 * a default so a component can render more easily: a segment whose surface was
 * never surveyed arrives at the map as unknown, and the map draws it as unknown.
 *
 * The elevation profile is likewise not smoothed or gap-filled here. Where the
 * source had no coverage the API reports a gap, and the chart draws a break
 * rather than a line across it.
 */

import { apiFetch } from '@/lib/api';
import type { MapSegment } from '@/components/RouteMap';
import type { ProfilePoint } from '@/components/ElevationProfile';
import type { TopologyNode } from '@/components/RouteTopology';

export interface Waypoint {
  readonly lat: number;
  readonly lon: number;
  /** "break" allows a u-turn; "through" pins the route through this point. */
  readonly kind: 'break' | 'through';
  readonly name?: string;
}

export interface ConstraintOutcome {
  readonly constraint_key: string;
  readonly stated_as: string | null;
  readonly requested: unknown;
  readonly observed: unknown;
  readonly segment_indices: number[];
  readonly distance_m: number;
  readonly detail: string;
}

export interface RouteValidation {
  readonly verdict: string;
  readonly checks: { check: string; outcome: string; detail: string }[];
  readonly violations: ConstraintOutcome[];
  readonly unevaluated: { key: string; detail: string }[];
  readonly unavailable_checks: { check: string; detail: string }[];
  readonly resolve_attempts: number;
  readonly exhausted_attempts: boolean;
  readonly summary: string;
}

interface SegmentPayload {
  index: number;
  start_distance_m: number;
  distance_m: number;
  surface: { family: string; status: string };
  bicycle_access: { value: string; status: string };
  road_class: { value: string; status: string };
  cycle_lane: { value: string | null; status: string };
  max_grade_percent: number | null;
  elevation_status: string;
  is_ferry: boolean | null;
  graph_edge_id: string | null;
}

interface GeneratePayload {
  distance_m: number;
  duration_s: number | null;
  validation: RouteValidation;
  composition: {
    surface_m: Record<string, number>;
    road_class_m: Record<string, number>;
    cycle_infrastructure_m: Record<string, number>;
  };
  segments: SegmentPayload[];
  geometry: { type: 'LineString'; coordinates: [number, number][] };
  elevation: {
    ascent_m: number | null;
    descent_m: number | null;
    coverage: { ratio: number; gap_distance_m: number; complete: boolean };
    max_climb_percent: number | null;
    max_gradient_percent: number | null;
    notes: string[];
    method: Record<string, unknown>;
  } | null;
  elevation_note?: string;
  provenance: Record<string, unknown>;
}

export interface GeneratedRoute {
  readonly distanceM: number;
  readonly durationS: number | null;
  readonly validation: RouteValidation;
  readonly segments: MapSegment[];
  readonly profilePoints: ProfilePoint[];
  readonly topologyNodes: TopologyNode[];
  readonly ascentM: number | null;
  readonly descentM: number | null;
  readonly maxGradePercent: number | null;
  /**
   * The window the gradient was measured over. A gradient figure without it is
   * not comparable with anything, so it travels with the number (§11.4).
   */
  readonly gradientWindowM: number | null;
  readonly elevationGapM: number;
  /** Present when no elevation source is configured, so the UI can say why. */
  readonly elevationNote: string | null;
  readonly surfaceCompositionM: Record<string, number>;
  readonly provenance: Record<string, unknown>;
  /** Credited wherever the route is shown (§20.7). */
  readonly sources: { name: string; licence: string; lastImport?: string | null }[];
}

/**
 * Split the route geometry back into per-segment coordinates.
 *
 * The API sends the geometry once and each segment's extent as a distance
 * range, which keeps the payload from repeating shared vertices. Cutting on
 * cumulative distance reproduces the segments exactly, and every boundary
 * vertex belongs to both neighbours so the drawn line has no gaps at the joins.
 */
export function sliceGeometry(
  coordinates: readonly [number, number][],
  segments: readonly SegmentPayload[],
): Map<number, [number, number][]> {
  const result = new Map<number, [number, number][]>();
  if (coordinates.length < 2 || segments.length === 0) return result;

  const cumulative: number[] = [0];
  for (let i = 1; i < coordinates.length; i += 1) {
    const previous = coordinates[i - 1]!;
    const current = coordinates[i]!;
    cumulative.push(cumulative[i - 1]! + haversineM(previous, current));
  }

  for (const segment of segments) {
    const start = segment.start_distance_m;
    const end = start + segment.distance_m;
    const points: [number, number][] = [];

    for (let i = 0; i < coordinates.length; i += 1) {
      // A metre of slack at each end: the distances came from the API's own
      // measurement of the same shape, and exact float equality at a boundary
      // would drop the shared vertex and leave a visible gap at every join.
      if (cumulative[i]! >= start - 1 && cumulative[i]! <= end + 1) {
        points.push(coordinates[i]!);
      }
    }
    // A short segment can fall between two vertices. Rather than drop it, it
    // borrows the pair that brackets it, so every segment is drawable.
    if (points.length < 2) {
      const nearest = nearestIndex(cumulative, start);
      const lo = Math.max(0, Math.min(nearest, coordinates.length - 2));
      points.length = 0;
      points.push(coordinates[lo]!, coordinates[lo + 1]!);
    }
    result.set(segment.index, points);
  }

  return result;
}

function nearestIndex(cumulative: readonly number[], target: number): number {
  let best = 0;
  let bestGap = Number.POSITIVE_INFINITY;
  for (let i = 0; i < cumulative.length; i += 1) {
    const gap = Math.abs(cumulative[i]! - target);
    if (gap < bestGap) {
      bestGap = gap;
      best = i;
    }
  }
  return best;
}

const EARTH_RADIUS_M = 6371008.8;

function haversineM(a: readonly [number, number], b: readonly [number, number]): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(Math.min(1, h)));
}

export interface GenerateOptions {
  readonly maxGradientPercent?: number | null;
  readonly maxUnpavedM?: number | null;
  readonly maxUnknownSurfaceM?: number | null;
  readonly avoidHills?: number | null;
  readonly bicycleType?: string;
}

export async function generateRoute(
  waypoints: readonly Waypoint[],
  options: GenerateOptions = {},
): Promise<GeneratedRoute> {
  const payload = await apiFetch<GeneratePayload>('/v1/routes/generate', {
    method: 'POST',
    body: JSON.stringify({
      locations: waypoints.map((point) => ({
        lat: point.lat,
        lon: point.lon,
        kind: point.kind,
        name: point.name,
      })),
      preferences: {
        bicycle_type: options.bicycleType ?? 'hybrid',
        avoid_hills: options.avoidHills ?? null,
      },
      constraints: {
        max_gradient_percent: options.maxGradientPercent ?? null,
        max_unpaved_m: options.maxUnpavedM ?? null,
        max_unknown_surface_m: options.maxUnknownSurfaceM ?? null,
      },
    }),
  });

  const geometry = sliceGeometry(payload.geometry.coordinates, payload.segments);

  const segments: MapSegment[] = payload.segments.map((segment) => ({
    index: segment.index,
    startDistanceM: segment.start_distance_m,
    distanceM: segment.distance_m,
    coordinates: geometry.get(segment.index) ?? [],
    surface: segment.surface,
    bicycleAccess: segment.bicycle_access,
    roadClass: segment.road_class,
    cycleLane: segment.cycle_lane,
    maxGradePercent: segment.max_grade_percent,
    elevationStatus: segment.elevation_status,
    isFerry: segment.is_ferry,
  }));

  return {
    distanceM: payload.distance_m,
    durationS: payload.duration_s,
    validation: payload.validation,
    segments,
    profilePoints: profileFrom(payload),
    topologyNodes: topologyFrom(payload),
    ascentM: payload.elevation?.ascent_m ?? null,
    descentM: payload.elevation?.descent_m ?? null,
    // The direction-agnostic figure: what the gradient constraint checks.
    maxGradePercent: payload.elevation?.max_gradient_percent ?? null,
    gradientWindowM: windowFrom(payload),
    elevationGapM: payload.elevation?.coverage.gap_distance_m ?? 0,
    elevationNote: payload.elevation ? null : (payload.elevation_note ?? null),
    surfaceCompositionM: payload.composition.surface_m,
    provenance: payload.provenance,
    sources: sourcesFrom(payload),
  };
}

/**
 * The shortest grade window the analysis actually used.
 *
 * Taken from the response rather than assumed, because the API widens the
 * window to whatever the elevation source can support and reports which
 * windows it had to drop.
 */
function windowFrom(payload: GeneratePayload): number | null {
  const windows = payload.elevation?.method?.grade_windows_m;
  if (!Array.isArray(windows) || windows.length === 0) return null;
  return Math.min(...(windows as number[]));
}

/**
 * Sources credited with this route.
 *
 * Derived from what the response says actually contributed. Listing a source
 * that supplied nothing would be an attribution for work it did not do, and
 * omitting one that did is a licence breach.
 */
function sourcesFrom(payload: GeneratePayload): {
  name: string;
  licence: string;
  lastImport?: string | null;
}[] {
  const sources = [
    { name: 'OpenStreetMap contributors', licence: 'ODbL-1.0' },
  ];
  if (payload.elevation) {
    sources.push({
      name: String(payload.provenance.elevation_source ?? 'elevation source'),
      licence: 'terms not verified',
    });
  }
  return sources;
}

/**
 * Build the profile from per-segment gradients.
 *
 * A segment with no elevation coverage contributes a null point rather than
 * being skipped, so the chart draws a break there. Skipping it would join the
 * two measured sides into a line that looks like measured ground.
 */
function profileFrom(payload: GeneratePayload): ProfilePoint[] {
  if (!payload.elevation) return [];
  return payload.segments.map((segment) => ({
    distanceM: segment.start_distance_m,
    elevationM: null,
    gradePercent: segment.elevation_status === 'known' ? segment.max_grade_percent : null,
  }));
}

/**
 * The schematic view: start, finish, and the things that interrupt a ride.
 *
 * Ferries and surface changes are included because both are decisions a rider
 * has to make, and both are visible in the data. Nothing is inferred — a
 * surface change is only recorded between two segments that were *both*
 * surveyed, since a transition from known to unknown is a change in what is
 * recorded, not a change in the road.
 */
function topologyFrom(payload: GeneratePayload): TopologyNode[] {
  const nodes: TopologyNode[] = [
    { id: 'start', kind: 'start', label: 'Start', distanceM: 0 },
  ];

  let previous: SegmentPayload | null = null;
  for (const segment of payload.segments) {
    if (segment.is_ferry === true && previous?.is_ferry !== true) {
      nodes.push({
        id: `ferry-${segment.index}`,
        kind: 'ferry',
        label: 'Ferry crossing',
        distanceM: segment.start_distance_m,
        detail: 'A crossing, not a road. Check the operator for sailings.',
      });
    }

    if (
      previous &&
      previous.surface.status === 'known' &&
      segment.surface.status === 'known' &&
      previous.surface.family !== segment.surface.family
    ) {
      nodes.push({
        id: `surface-${segment.index}`,
        kind: 'surface-change',
        label: `Surface changes to ${segment.surface.family}`,
        distanceM: segment.start_distance_m,
      });
    }
    previous = segment;
  }

  nodes.push({
    id: 'finish',
    kind: 'finish',
    label: 'Finish',
    distanceM: payload.distance_m,
  });
  return nodes;
}
