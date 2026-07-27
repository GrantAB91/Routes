/**
 * Route colouring and geometry slicing (§10.7, §2.6).
 *
 * Both are pure functions, so they are tested without a browser or a map. What
 * they have to get right is the same thing everywhere else in Contour has to:
 * a segment nobody surveyed must not be drawn as though somebody had.
 */

import { describe, expect, it } from 'vitest';

import { bandForSegment, toFeatureCollection, type MapSegment } from '@/components/RouteMap';
import { COLOUR_SCALES } from '@/lib/map-modes';
import { sliceGeometry } from '@/lib/route';

function segment(overrides: Partial<MapSegment> = {}): MapSegment {
  return {
    index: 0,
    startDistanceM: 0,
    distanceM: 100,
    coordinates: [
      [-9.81, 53.76],
      [-9.8, 53.762],
    ],
    surface: { family: 'unknown', status: 'unknown' },
    bicycleAccess: { value: 'unknown', status: 'unknown' },
    roadClass: { value: 'unknown', status: 'unknown' },
    cycleLane: { value: null, status: 'unknown' },
    maxGradePercent: null,
    elevationStatus: 'unknown',
    isFerry: null,
    ...overrides,
  };
}

describe('band selection', () => {
  it('draws an unsurveyed segment as unknown, not as the lowest band', () => {
    // The single most misleading thing the map could do: render a lane nobody
    // has surveyed as flat, paved and green.
    const band = bandForSegment(COLOUR_SCALES.gradient, segment());

    expect(band.key).toBe('unknown');
  });

  it('ignores a gradient value when the elevation status is not known', () => {
    // A stale or defaulted number must not colour the route just because the
    // field happens to be populated.
    const band = bandForSegment(
      COLOUR_SCALES.gradient,
      segment({ maxGradePercent: 2, elevationStatus: 'unknown' }),
    );

    expect(band.key).toBe('unknown');
  });

  it('uses the measured gradient when it is known', () => {
    const band = bandForSegment(
      COLOUR_SCALES.gradient,
      segment({ maxGradePercent: 7.5, elevationStatus: 'known' }),
    );

    expect(band.key).toBe('6-9');
  });

  it('ignores a surface family when the surface was never recorded', () => {
    const band = bandForSegment(
      COLOUR_SCALES.surface,
      segment({ surface: { family: 'paved', status: 'unknown' } }),
    );

    expect(band.key).toBe('unknown');
  });

  it('reads confidence from how much was recorded, not from any value', () => {
    const fully = bandForSegment(
      COLOUR_SCALES.confidence,
      segment({
        surface: { family: 'paved', status: 'known' },
        bicycleAccess: { value: 'yes', status: 'known' },
        roadClass: { value: 'tertiary', status: 'known' },
        elevationStatus: 'known',
      }),
    );
    const none = bandForSegment(COLOUR_SCALES.confidence, segment());

    expect(fully.key).toBe('high');
    expect(none.key).toBe('unknown');
  });
});

describe('feature collection', () => {
  it('gives every feature a dash pattern as well as a colour', () => {
    // §10.7: colour is never the only channel. A feature without a pattern is
    // invisible to a rider who cannot distinguish the colours.
    const collection = toFeatureCollection(
      [segment({ surface: { family: 'paved', status: 'known' } })],
      COLOUR_SCALES.surface,
    );

    for (const feature of collection.features) {
      expect(feature.properties?.colour).toBeTruthy();
      expect(Array.isArray(feature.properties?.dash)).toBe(true);
      expect((feature.properties?.dash as number[]).length).toBeGreaterThan(0);
    }
  });

  it('never emits a null dash, which would leave the previous pattern in place', () => {
    const collection = toFeatureCollection([segment()], null);

    expect(collection.features[0]?.properties?.dash).toEqual([1]);
  });

  it('drops a segment with too few points to draw rather than emitting a degenerate line', () => {
    const collection = toFeatureCollection([segment({ coordinates: [[-9.8, 53.7]] })], null);

    expect(collection.features).toHaveLength(0);
  });

  it('carries the segment index so a click can identify it', () => {
    const collection = toFeatureCollection([segment({ index: 42 })], null);

    expect(collection.features[0]?.properties?.index).toBe(42);
  });
});

describe('geometry slicing', () => {
  // Four points about 1 km apart along a meridian.
  const coordinates: [number, number][] = [
    [-9.8, 53.76],
    [-9.8, 53.769],
    [-9.8, 53.778],
    [-9.8, 53.787],
  ];

  const payload = (index: number, start: number, distance: number) => ({
    index,
    start_distance_m: start,
    distance_m: distance,
    surface: { family: 'unknown', status: 'unknown' },
    bicycle_access: { value: 'unknown', status: 'unknown' },
    road_class: { value: 'unknown', status: 'unknown' },
    cycle_lane: { value: null, status: 'unknown' },
    max_grade_percent: null,
    elevation_status: 'unknown',
    is_ferry: null,
    graph_edge_id: null,
  });

  it('gives adjacent segments the vertex they share', () => {
    // Without the shared vertex the drawn line has a visible gap at every
    // join, which reads as a break in the route rather than a segment boundary.
    const sliced = sliceGeometry(coordinates, [payload(0, 0, 1000), payload(1, 1000, 2000)]);

    const first = sliced.get(0)!;
    const second = sliced.get(1)!;
    expect(first[first.length - 1]).toEqual(second[0]);
  });

  it('gives every segment at least two points so it can be drawn', () => {
    const sliced = sliceGeometry(coordinates, [
      payload(0, 0, 5),
      payload(1, 5, 1500),
      payload(2, 1505, 1495),
    ]);

    for (const points of sliced.values()) {
      expect(points.length).toBeGreaterThanOrEqual(2);
    }
  });

  it('returns nothing rather than guessing when there is no geometry', () => {
    expect(sliceGeometry([], [payload(0, 0, 100)]).size).toBe(0);
  });
});
