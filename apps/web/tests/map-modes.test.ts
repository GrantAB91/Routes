/**
 * Colour scale and map mode tests (§10.7).
 *
 * The invariants here are what keep colour from being the only channel and keep
 * missing data from being rendered as a real value. They are cheap to break by
 * adding a band and easy to miss in review, so they are asserted structurally
 * across every scale rather than spot-checked.
 */

import { describe, expect, it } from 'vitest';

import {
  COLOUR_SCALES,
  GRADIENT_SCALE,
  MAP_MODES,
  SURFACE_SCALE,
  bandFor,
  bandForKey,
  mapMode,
  unknownBand,
} from '../src/lib/map-modes';

const ALL_SCALES = Object.values(COLOUR_SCALES);

describe('colour scales', () => {
  it('every scale can represent unknown', () => {
    // Without this a scale silently classifies unsurveyed data into a real
    // band — an unknown surface rendered as paved, an unknown gradient as flat.
    for (const scale of ALL_SCALES) {
      expect(() => unknownBand(scale), scale.id).not.toThrow();
    }
  });

  it('every band carries a non-colour channel', () => {
    for (const scale of ALL_SCALES) {
      for (const band of scale.bands) {
        // A pattern of null means a solid line, which is itself distinguishable
        // as long as it is used once per scale; what matters is that the band
        // also carries text.
        expect(band.label.length, `${scale.id}/${band.key}`).toBeGreaterThan(0);
        expect(band.description.length, `${scale.id}/${band.key}`).toBeGreaterThan(0);
      }
    }
  });

  it('no scale relies on a single solid line to distinguish two bands', () => {
    for (const scale of ALL_SCALES) {
      const solid = scale.bands.filter((band) => band.pattern === null);
      expect(solid.length, `${scale.id} has ${solid.length} solid bands`).toBeLessThanOrEqual(1);
    }
  });

  it('every band uses a distinct colour', () => {
    for (const scale of ALL_SCALES) {
      const colours = new Set(scale.bands.map((band) => band.colour));
      expect(colours.size, scale.id).toBe(scale.bands.length);
    }
  });
});

describe('classification', () => {
  it('null classifies as unknown rather than the lowest band', () => {
    // The failure this prevents: an unsurveyed segment drawn as 0-3% green,
    // which reads as "flat and fine" instead of "nobody has measured this".
    expect(bandFor(GRADIENT_SCALE, null).key).toBe('unknown');
    expect(bandFor(GRADIENT_SCALE, undefined).key).toBe('unknown');
    expect(bandFor(GRADIENT_SCALE, Number.NaN).key).toBe('unknown');
  });

  it('classifies real gradients into their band', () => {
    expect(bandFor(GRADIENT_SCALE, 1.2).key).toBe('0-3');
    expect(bandFor(GRADIENT_SCALE, 7).key).toBe('6-9');
    expect(bandFor(GRADIENT_SCALE, 18).key).toBe('12+');
  });

  it('treats band boundaries consistently', () => {
    // 3% belongs to the 3-6 band, not to 0-3, so a segment cannot fall in two.
    expect(bandFor(GRADIENT_SCALE, 3).key).toBe('3-6');
    expect(bandFor(GRADIENT_SCALE, 6).key).toBe('6-9');
  });

  it('unrecognised categories classify as unknown', () => {
    expect(bandForKey(SURFACE_SCALE, 'something-new').key).toBe('unknown');
    expect(bandForKey(SURFACE_SCALE, null).key).toBe('unknown');
  });

  it('distinguishes recorded absence from missing data', () => {
    // "A source says there is no cycle lane here" and "nobody recorded whether
    // there is one" are different facts and must not share a band.
    const infrastructure = COLOUR_SCALES['cycle-infrastructure'];
    const absent = bandForKey(infrastructure, 'absent');
    const unknown = bandForKey(infrastructure, null);

    expect(absent.key).toBe('absent');
    expect(unknown.key).toBe('unknown');
    expect(absent.colour).not.toBe(unknown.colour);
  });
});

describe('map modes', () => {
  it('provides every mode §10.1 requires', () => {
    const ids = MAP_MODES.map((mode) => mode.id);
    for (const required of [
      'standard',
      'cycling-network',
      'topographic',
      'satellite',
      'terrain-3d',
      'slope',
      'surface',
      'access',
      'road-class',
      'provenance',
      'confidence',
      'topology',
    ]) {
      expect(ids).toContain(required);
    }
  });

  it('marks which modes need a basemap so the app can say what is missing', () => {
    expect(mapMode('standard').requiresBasemap).toBe(true);
    // Contour's own analysis layers must remain usable with no basemap at all.
    expect(mapMode('surface').requiresBasemap).toBe(false);
    expect(mapMode('slope').requiresBasemap).toBe(false);
    expect(mapMode('provenance').requiresBasemap).toBe(false);
  });

  it('marks topology as a schematic rather than a map', () => {
    expect(mapMode('topology').isSchematic).toBe(true);
    expect(mapMode('standard').isSchematic).toBe(false);
  });

  it('labels the road class mode as a traffic proxy', () => {
    // §7.7: road class may stand in for traffic exposure only when labelled.
    expect(mapMode('road-class').description).toMatch(/proxy/i);
    expect(mapMode('road-class').description).toMatch(/no traffic data/i);
  });

  it('every mode explains itself', () => {
    for (const mode of MAP_MODES) {
      expect(mode.label.length, mode.id).toBeGreaterThan(0);
      expect(mode.description.length, mode.id).toBeGreaterThan(10);
    }
  });
});
