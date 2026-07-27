'use client';

/**
 * The map canvas (§10.1-10.7, §5.4, §5.10).
 *
 * Contour draws its own layers, so a basemap is context rather than a
 * dependency: with none configured the route, its colouring, the segment
 * inspector and every measurement still work over a blank canvas, and the
 * interface says so instead of showing grey that reads as a loading failure.
 *
 * Colour is never the only channel (§10.7). Each segment is drawn in its band's
 * colour *and* its band's dash pattern, both taken from the same definition the
 * legend renders, so the route stays readable in greyscale and under colour
 * vision deficiency. A segment whose attribute was never recorded is drawn in
 * the unknown band — heavily dashed, deliberately unlike any data colour — and
 * never in the lowest band, which would render an unsurveyed lane as flat,
 * paved and green.
 *
 * Everything the map offers by pointer is also reachable without one. Segments
 * can be stepped through from the keyboard and the current one is announced,
 * because a map that can only be inspected by hovering excludes anyone not
 * using a mouse (§18).
 */

import type { FeatureCollection } from 'geojson';
import {
  AttributionControl,
  LngLatBounds,
  Map as MapLibreMap,
  Marker,
  NavigationControl,
  ScaleControl,
  type GeoJSONSource,
  type MapMouseEvent,
  type StyleSpecification,
} from 'maplibre-gl';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  type ColourScale,
  type ScaleBand,
  bandForKey,
  bandFor,
  type MapMode,
  unknownBand,
} from '@/lib/map-modes';
import { type BasemapState, BLANK_STYLE, styleFor } from '@/lib/map-provider';

export interface MapSegment {
  readonly index: number;
  readonly startDistanceM: number;
  readonly distanceM: number;
  readonly coordinates: readonly (readonly [number, number])[];
  readonly surface: { family: string; status: string };
  readonly bicycleAccess: { value: string; status: string };
  readonly roadClass: { value: string; status: string };
  readonly cycleLane: { value: string | null; status: string };
  readonly maxGradePercent: number | null;
  readonly elevationStatus: string;
  readonly isFerry: boolean | null;
  readonly stageIndex?: number | null;
  readonly sourceSlug?: string | null;
}

export interface MapWaypoint {
  readonly lat: number;
  readonly lon: number;
  readonly kind: 'break' | 'through';
  readonly name?: string;
}

interface RouteMapProps {
  readonly segments: readonly MapSegment[];
  readonly mode: MapMode;
  readonly scale: ColourScale | null;
  readonly basemap: BasemapState;
  /** Distance hovered on the elevation profile, linked to the map (§10.5). */
  readonly hoverDistanceM: number | null;
  readonly onSelectSegment?: (segment: MapSegment | null) => void;
  /**
   * Waypoints the rider has placed. Rendered as draggable markers; dragging one
   * reports a new position and the caller re-routes. The map never edits
   * geometry itself — it changes the request and asks again (§7.1, §9.1).
   */
  readonly waypoints?: readonly MapWaypoint[];
  readonly onAddWaypoint?: (point: { lat: number; lon: number }) => void;
  readonly onMoveWaypoint?: (index: number, point: { lat: number; lon: number }) => void;
  readonly onRemoveWaypoint?: (index: number) => void;
}

const SOURCE_ID = 'contour-route';
const LAYER_CASING = 'contour-route-casing';
const LAYER_LINE = 'contour-route-line';
const LAYER_HIGHLIGHT = 'contour-route-highlight';

/**
 * Which band a segment falls in, for the active scale.
 *
 * Every branch that reads an attribute checks its status first. Reading the
 * value without the status is what turns "nobody surveyed this" into a
 * confident colour, which is the failure this whole file is arranged against.
 */
export function bandForSegment(scale: ColourScale, segment: MapSegment): ScaleBand {
  switch (scale.id) {
    case 'gradient':
      return segment.elevationStatus === 'known'
        ? bandFor(scale, segment.maxGradePercent)
        : unknownBand(scale);

    case 'surface':
      return segment.surface.status === 'known'
        ? bandForKey(scale, segment.surface.family)
        : unknownBand(scale);

    case 'access':
      return segment.bicycleAccess.status === 'known'
        ? bandForKey(scale, segment.bicycleAccess.value)
        : unknownBand(scale);

    case 'cycle-infrastructure':
      return segment.cycleLane.status === 'known'
        ? bandForKey(scale, segment.cycleLane.value)
        : unknownBand(scale);

    case 'confidence': {
      // Confidence is how much of this segment was actually recorded, so it is
      // derived from the statuses rather than from any value.
      const statuses = [
        segment.surface.status,
        segment.bicycleAccess.status,
        segment.roadClass.status,
        segment.elevationStatus,
      ];
      const known = statuses.filter((status) => status === 'known').length;
      if (known === statuses.length) return bandForKey(scale, 'high');
      if (known === 0) return unknownBand(scale);
      return bandForKey(scale, known >= statuses.length / 2 ? 'medium' : 'low');
    }

    case 'source':
      return bandForKey(scale, segment.sourceSlug ?? null);

    case 'stage':
      if (segment.stageIndex === null || segment.stageIndex === undefined) {
        return unknownBand(scale);
      }
      return bandForKey(scale, segment.stageIndex % 2 === 0 ? 'odd' : 'even');

    case 'elevation':
      return segment.elevationStatus === 'known'
        ? bandFor(scale, segment.maxGradePercent)
        : unknownBand(scale);

    default:
      return unknownBand(scale);
  }
}

export function toFeatureCollection(
  segments: readonly MapSegment[],
  scale: ColourScale | null,
): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: segments
      .filter((segment) => segment.coordinates.length >= 2)
      .map((segment) => {
        const band = scale ? bandForSegment(scale, segment) : null;
        return {
          type: 'Feature' as const,
          id: segment.index,
          geometry: {
            type: 'LineString' as const,
            coordinates: segment.coordinates.map(([lon, lat]) => [lon, lat]),
          },
          properties: {
            index: segment.index,
            band: band?.key ?? 'route',
            colour: band?.colour ?? '#1a5f8b',
            // MapLibre has no "no dash" value, so a solid line is expressed as
            // a single long dash. Passing null leaves the previous pattern in
            // place, which silently mislabels the segment.
            dash: band?.pattern ?? [1],
            label: band?.label ?? 'Route',
          },
        };
      }),
  };
}

function boundsOf(segments: readonly MapSegment[]): LngLatBounds | null {
  const first = segments.find((segment) => segment.coordinates.length > 0);
  if (!first) return null;

  const bounds = new LngLatBounds(
    first.coordinates[0] as [number, number],
    first.coordinates[0] as [number, number],
  );
  for (const segment of segments) {
    for (const point of segment.coordinates) {
      bounds.extend(point as [number, number]);
    }
  }
  return bounds;
}

function segmentAt(
  segments: readonly MapSegment[],
  distanceM: number | null,
): MapSegment | null {
  if (distanceM === null) return null;
  return (
    segments.find(
      (segment) =>
        distanceM >= segment.startDistanceM &&
        distanceM <= segment.startDistanceM + segment.distanceM,
    ) ?? null
  );
}

/** What the inspector says about one segment, with every unknown named. */
export function describeSegment(segment: MapSegment): string[] {
  const say = (label: string, value: string, status: string) =>
    status === 'known'
      ? `${label}: ${value}`
      : `${label}: not recorded by any source here`;

  const lines = [
    say('Surface', segment.surface.family, segment.surface.status),
    say('Bicycle access', segment.bicycleAccess.value, segment.bicycleAccess.status),
    say('Road class', segment.roadClass.value, segment.roadClass.status),
    say('Cycle infrastructure', segment.cycleLane.value ?? '', segment.cycleLane.status),
    segment.elevationStatus === 'known' && segment.maxGradePercent !== null
      ? `Steepest gradient: ${segment.maxGradePercent.toFixed(1)}%`
      : 'Steepest gradient: no elevation coverage here',
  ];

  if (segment.isFerry === true) lines.push('This section is a ferry crossing.');
  return lines;
}

export function RouteMap({
  segments,
  mode,
  scale,
  basemap,
  hoverDistanceM,
  onSelectSegment,
  waypoints = [],
  onAddWaypoint,
  onMoveWaypoint,
  onRemoveWaypoint,
}: RouteMapProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  const markers = useRef<Marker[]>([]);
  const [ready, setReady] = useState(false);
  const [clickedIndex, setClickedIndex] = useState<number | null>(null);

  const collection = useMemo(() => toFeatureCollection(segments, scale), [segments, scale]);

  // The highlighted segment is whichever the rider clicked, or — when they have
  // clicked nothing — whichever the elevation profile is pointing at (§10.5).
  // Derived during render rather than copied into state by an effect, which
  // would render one frame of stale highlight on every hover.
  const selected = useMemo(() => {
    if (clickedIndex !== null) {
      return segments.find((segment) => segment.index === clickedIndex) ?? null;
    }
    return segmentAt(segments, hoverDistanceM);
  }, [segments, clickedIndex, hoverDistanceM]);

  const style: StyleSpecification | string = useMemo(
    () => (basemap.available ? styleFor(basemap) : BLANK_STYLE),
    [basemap],
  );

  // -- lifecycle -------------------------------------------------------------

  useEffect(() => {
    if (!container.current || map.current) return;

    const instance = new MapLibreMap({
      container: container.current,
      style,
      center: [-9.5, 53.5],
      zoom: 7,
      attributionControl: false,
      // Contour's own layers carry the meaning; a pitched view of a flat line
      // adds nothing outside the 3D mode, which enables it explicitly.
      pitch: 0,
    });

    instance.addControl(new NavigationControl({ visualizePitch: true }), 'top-right');
    instance.addControl(
      new AttributionControl({ compact: true, customAttribution: basemap.attribution }),
      'bottom-right',
    );
    instance.addControl(new ScaleControl({ unit: 'metric' }), 'bottom-left');

    instance.on('load', () => setReady(true));
    map.current = instance;

    return () => {
      instance.remove();
      map.current = null;
      setReady(false);
    };
    // The style is applied by its own effect; recreating the map on a style
    // change would lose the viewport the rider had set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!map.current || !ready) return;
    map.current.setStyle(style);
  }, [style, ready]);

  // -- route layers ----------------------------------------------------------

  const draw = useCallback(() => {
    const instance = map.current;
    if (!instance || !instance.isStyleLoaded()) return;

    const existing = instance.getSource(SOURCE_ID);
    if (existing) {
      (existing as GeoJSONSource).setData(collection);
      return;
    }

    instance.addSource(SOURCE_ID, { type: 'geojson', data: collection });

    // A casing under the coloured line keeps every band legible over both a
    // pale and a dark basemap without changing the band colours themselves.
    instance.addLayer({
      id: LAYER_CASING,
      type: 'line',
      source: SOURCE_ID,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': '#ffffff',
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 4, 14, 9],
        'line-opacity': 0.85,
      },
    });

    instance.addLayer({
      id: LAYER_LINE,
      type: 'line',
      source: SOURCE_ID,
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: {
        'line-color': ['get', 'colour'],
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 2, 14, 5],
        // The second, non-colour channel required by §10.7.
        'line-dasharray': ['get', 'dash'],
      },
    });

    instance.addLayer({
      id: LAYER_HIGHLIGHT,
      type: 'line',
      source: SOURCE_ID,
      filter: ['==', ['get', 'index'], -1],
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': '#111418',
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 6, 14, 12],
        'line-opacity': 0.35,
      },
    });
  }, [collection]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;

    draw();
    // setStyle discards every layer, so they are re-added when the new style
    // finishes loading rather than only on first load.
    instance.on('styledata', draw);
    return () => {
      instance.off('styledata', draw);
    };
  }, [draw, ready]);

  // -- terrain ---------------------------------------------------------------

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;

    if (!mode.requiresTerrain || !basemap.terrainUrl) {
      instance.setTerrain(null);
      instance.easeTo({ pitch: 0, duration: 300 });
      return;
    }

    if (!instance.getSource('contour-terrain')) {
      instance.addSource('contour-terrain', {
        type: 'raster-dem',
        url: basemap.terrainUrl,
        tileSize: 256,
      });
    }
    instance.setTerrain({ source: 'contour-terrain', exaggeration: 1 });
    instance.easeTo({ pitch: 60, duration: 600 });
  }, [mode, basemap, ready]);

  // -- framing ---------------------------------------------------------------

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready || segments.length === 0) return;

    const bounds = boundsOf(segments);
    if (bounds) instance.fitBounds(bounds, { padding: 48, duration: 600 });
  }, [segments, ready]);

  // -- selection -------------------------------------------------------------

  const select = useCallback(
    (index: number | null) => {
      setClickedIndex(index);
      onSelectSegment?.(segments.find((segment) => segment.index === index) ?? null);
    },
    [segments, onSelectSegment],
  );

  // Push the derived selection at the map. This is the effect's proper job:
  // synchronising an external system with React state, not producing state.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready || !instance.getLayer(LAYER_HIGHLIGHT)) return;
    instance.setFilter(LAYER_HIGHLIGHT, ['==', ['get', 'index'], selected?.index ?? -1]);
  }, [selected, ready]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;

    const onClick = (event: MapMouseEvent) => {
      const hits = instance.queryRenderedFeatures(event.point, { layers: [LAYER_LINE] });
      const index = hits[0]?.properties?.index;
      select(typeof index === 'number' ? index : null);
    };
    const enter = () => {
      instance.getCanvas().style.cursor = 'pointer';
    };
    const leave = () => {
      instance.getCanvas().style.cursor = '';
    };

    instance.on('click', onClick);
    instance.on('mouseenter', LAYER_LINE, enter);
    instance.on('mouseleave', LAYER_LINE, leave);
    return () => {
      instance.off('click', onClick);
      instance.off('mouseenter', LAYER_LINE, enter);
      instance.off('mouseleave', LAYER_LINE, leave);
    };
  }, [select, ready]);

  // -- waypoints -------------------------------------------------------------

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready) return;

    for (const marker of markers.current) marker.remove();
    markers.current = [];

    waypoints.forEach((waypoint, index) => {
      const element = document.createElement('button');
      element.type = 'button';
      element.className = 'map-waypoint';
      element.textContent = String(index + 1);
      const position =
        index === 0
          ? 'Start'
          : index === waypoints.length - 1
            ? 'Finish'
            : `Via point ${index}`;
      element.setAttribute(
        'aria-label',
        `${position}${waypoint.name ? `, ${waypoint.name}` : ''}. Activate to remove.`,
      );
      element.addEventListener('click', (event) => {
        event.stopPropagation();
        onRemoveWaypoint?.(index);
      });

      const marker = new Marker({ element, draggable: Boolean(onMoveWaypoint) })
        .setLngLat([waypoint.lon, waypoint.lat])
        .addTo(instance);

      marker.on('dragend', () => {
        const { lat, lng } = marker.getLngLat();
        onMoveWaypoint?.(index, { lat, lon: lng });
      });

      markers.current.push(marker);
    });

    return () => {
      for (const marker of markers.current) marker.remove();
      markers.current = [];
    };
  }, [waypoints, ready, onMoveWaypoint, onRemoveWaypoint]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready || !onAddWaypoint) return;

    const onMapClick = (event: MapMouseEvent) => {
      // A click on the route selects a segment; only empty map adds a point,
      // so inspecting the route cannot silently reshape it.
      const hits = instance.queryRenderedFeatures(event.point, { layers: [LAYER_LINE] });
      if (hits.length > 0) return;
      onAddWaypoint({ lat: event.lngLat.lat, lon: event.lngLat.lng });
    };

    instance.on('click', onMapClick);
    return () => {
      instance.off('click', onMapClick);
    };
  }, [onAddWaypoint, ready]);

  const step = useCallback(
    (delta: number) => {
      if (segments.length === 0) return;
      const current = selected?.index ?? -1;
      const next = Math.min(segments.length - 1, Math.max(0, current + delta));
      select(segments[next]!.index);
    },
    [segments, selected, select],
  );

  return (
    <div className="map-canvas-wrapper">
      <div
        ref={container}
        className="map-canvas"
        role="application"
        aria-label={`Map, ${mode.label} mode. ${mode.description}`}
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
            event.preventDefault();
            step(1);
          } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
            event.preventDefault();
            step(-1);
          } else if (event.key === 'Escape') {
            select(null);
          }
        }}
      />

      {segments.length === 0 ? (
        <p className="map-notice">
          <strong>No route is shown yet.</strong> Describe a journey and generate a
          route, or import a file, and it will be drawn here.
        </p>
      ) : null}

      <p className="visually-hidden">
        The route has {segments.length} sections. Use the arrow keys to step through
        them; each one is described as it is selected.
      </p>

      {selected ? (
        <div className="segment-inspector" role="status" aria-live="polite">
          <h3>
            Section {selected.index + 1} of {segments.length}
          </h3>
          <p className="segment-inspector__distance">
            {(selected.startDistanceM / 1000).toFixed(1)} km to{' '}
            {((selected.startDistanceM + selected.distanceM) / 1000).toFixed(1)} km
          </p>
          <ul>
            {describeSegment(selected).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <button type="button" onClick={() => select(null)}>
            Clear selection
          </button>
        </div>
      ) : null}
    </div>
  );
}
