/**
 * Basemap provider selection (§5.4, §5.9, §5.10).
 *
 * Contour renders its own vector layers itself. A basemap is context, not a
 * dependency, so when none is configured the map still works: the route, its
 * colouring, the segment inspector and every analysis remain fully usable over
 * a blank canvas, and the interface says plainly that no basemap is configured
 * rather than showing an empty grey area that reads as a loading failure.
 *
 * The public OpenStreetMap tile service is deliberately absent from the
 * options. The OSMF tile usage policy does not permit it as an application
 * backend, and §5.9 forbids it regardless of whether it would technically work.
 */

import type { StyleSpecification } from 'maplibre-gl';

export type MapProviderId = 'maptiler' | 'protomaps' | 'self-hosted' | 'none';

export interface BasemapState {
  readonly provider: MapProviderId;
  readonly available: boolean;
  /** Shown in the interface when unavailable. Names the exact missing setting. */
  readonly reason?: string;
  readonly remedy?: string;
  /** Attribution that must be displayed whenever this basemap is shown (§20.7). */
  readonly attribution?: string;
  /**
   * Raster-DEM tile source for the 3D terrain mode, when the provider serves
   * one. Terrain is a separate product from the basemap — a provider can serve
   * map tiles and no elevation tiles — so this is set only where terrain tiles
   * are actually available, and the 3D mode reports its absence rather than
   * rendering a flat surface that looks like terrain with no relief.
   */
  readonly terrainUrl?: string;
}

/** A style with no sources: Contour's own layers are added on top of this. */
export const BLANK_STYLE: StyleSpecification = {
  version: 8,
  name: 'Contour blank',
  // MapLibre requires a glyphs URL for any text layer. Contour renders its
  // labels as HTML markers rather than map symbols precisely so that no basemap
  // and no font server are required for the route to be readable.
  sources: {},
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: { 'background-color': '#eef1f4' },
    },
  ],
};

export function resolveBasemap(env: {
  provider?: string;
  maptilerKey?: string;
  pmtilesUrl?: string;
}): BasemapState {
  const provider = (env.provider ?? 'none') as MapProviderId;

  if (provider === 'maptiler') {
    if (!env.maptilerKey) {
      return {
        provider,
        available: false,
        reason: 'MapTiler is selected but no API key is set.',
        remedy: 'Set NEXT_PUBLIC_CONTOUR_MAPTILER_KEY, or set the provider to "none".',
      };
    }
    return {
      provider,
      available: true,
      attribution: '© MapTiler © OpenStreetMap contributors',
      // MapTiler publishes terrain-RGB tiles under the same key.
      terrainUrl: `https://api.maptiler.com/tiles/terrain-rgb-v2/tiles.json?key=${env.maptilerKey}`,
    };
  }

  if (provider === 'protomaps' || provider === 'self-hosted') {
    if (!env.pmtilesUrl) {
      return {
        provider,
        available: false,
        reason: `${provider} is selected but no tile URL is set.`,
        remedy: 'Set NEXT_PUBLIC_CONTOUR_PMTILES_URL, or set the provider to "none".',
      };
    }
    return {
      provider,
      available: true,
      attribution: '© OpenStreetMap contributors',
    };
  }

  return {
    provider: 'none',
    available: false,
    reason: 'No basemap provider is configured.',
    remedy:
      'Set NEXT_PUBLIC_CONTOUR_MAP_PROVIDER and its key or tile URL. The route, its colouring and all analysis work without one; only background context is missing.',
  };
}

export function basemapStateFromEnv(): BasemapState {
  return resolveBasemap({
    provider: process.env.NEXT_PUBLIC_CONTOUR_MAP_PROVIDER,
    maptilerKey: process.env.NEXT_PUBLIC_CONTOUR_MAPTILER_KEY,
    pmtilesUrl: process.env.NEXT_PUBLIC_CONTOUR_PMTILES_URL,
  });
}

export function styleFor(state: BasemapState): StyleSpecification | string {
  if (!state.available) return BLANK_STYLE;

  if (state.provider === 'maptiler') {
    const key = process.env.NEXT_PUBLIC_CONTOUR_MAPTILER_KEY;
    return `https://api.maptiler.com/maps/outdoor-v2/style.json?key=${key}`;
  }
  return process.env.NEXT_PUBLIC_CONTOUR_PMTILES_URL ?? BLANK_STYLE;
}
