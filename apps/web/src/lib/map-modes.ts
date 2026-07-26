/**
 * Map modes and route colouring (§10.1, §10.6, §10.7).
 *
 * §10.7 says colour must never be the only way information is communicated.
 * That is enforced structurally here rather than left to each component: every
 * band in every scale carries a `pattern`, a `label` and a `description`
 * alongside its colour, and the legend and accessible table are generated from
 * the same definition the map layer uses. A band cannot be added without its
 * non-colour channels, because the type requires them.
 *
 * The colours themselves are chosen to stay distinguishable under the common
 * forms of colour vision deficiency, and every scale includes an explicit
 * `unknown` band. Rendering unknown data in the same grey as "no data yet"
 * would hide the distinction the whole product rests on.
 */

export type MapModeId =
  | 'standard'
  | 'cycling-network'
  | 'topographic'
  | 'satellite'
  | 'terrain-3d'
  | 'slope'
  | 'surface'
  | 'access'
  | 'road-class'
  | 'provenance'
  | 'confidence'
  | 'topology';

export type ColourModeId =
  | 'gradient'
  | 'elevation'
  | 'surface'
  | 'cycle-infrastructure'
  | 'access'
  | 'source'
  | 'stage'
  | 'confidence';

/** A dash pattern in MapLibre `line-dasharray` units, or null for a solid line. */
export type LinePattern = number[] | null;

export interface ScaleBand {
  /** Stable key used by the map layer, the legend and the data table. */
  readonly key: string;
  readonly label: string;
  readonly colour: string;
  /**
   * The non-colour channel. Every band has one so the route stays readable in
   * greyscale, in print, and for a rider with colour vision deficiency.
   */
  readonly pattern: LinePattern;
  /** Plain-language explanation shown in the legend and read by screen readers. */
  readonly description: string;
  /** Lower bound for numeric scales, used to classify a segment. */
  readonly min?: number;
  readonly max?: number;
}

export interface ColourScale {
  readonly id: ColourModeId;
  readonly label: string;
  readonly summary: string;
  readonly bands: readonly ScaleBand[];
}

const UNKNOWN_BAND: ScaleBand = {
  key: 'unknown',
  label: 'Unknown',
  // Deliberately distinct from every data colour, and heavily dashed so it
  // reads as absent rather than as a low value.
  colour: '#9aa0a6',
  pattern: [1, 2],
  description:
    'No source records this attribute here. Contour does not guess, so this is not a low value — it is no value.',
};

export const GRADIENT_SCALE: ColourScale = {
  id: 'gradient',
  label: 'Gradient',
  summary:
    'Steepness over the shortest analysis window the elevation source supports. A gradient figure is meaningless without that window, which is shown with the route.',
  bands: [
    {
      key: '0-3',
      label: '0–3%',
      colour: '#1a7f37',
      pattern: null,
      description: 'Flat to gently rising.',
      min: 0,
      max: 3,
    },
    {
      key: '3-6',
      label: '3–6%',
      colour: '#7cb342',
      pattern: [6, 2],
      description: 'A steady drag.',
      min: 3,
      max: 6,
    },
    {
      key: '6-9',
      label: '6–9%',
      colour: '#e8a33d',
      pattern: [4, 2],
      description: 'A climb most riders will feel.',
      min: 6,
      max: 9,
    },
    {
      key: '9-12',
      label: '9–12%',
      colour: '#d1601f',
      pattern: [3, 3],
      description: 'Steep.',
      min: 9,
      max: 12,
    },
    {
      key: '12+',
      label: '12% and above',
      colour: '#a4232c',
      pattern: [2, 4],
      description: 'Very steep; may require walking on a loaded bicycle.',
      min: 12,
    },
    UNKNOWN_BAND,
  ],
};

export const SURFACE_SCALE: ColourScale = {
  id: 'surface',
  label: 'Surface',
  summary:
    'The surface a source recorded, not the routing engine’s generalisation of it. Where nothing was recorded the segment is unknown, never assumed paved.',
  bands: [
    {
      key: 'paved',
      label: 'Paved',
      colour: '#2d5f8b',
      pattern: null,
      description: 'A source records a sealed surface.',
    },
    {
      key: 'unpaved',
      label: 'Unpaved',
      colour: '#8a6d3b',
      pattern: [5, 3],
      description: 'A source records gravel, dirt, compacted or similar.',
    },
    UNKNOWN_BAND,
  ],
};

export const ACCESS_SCALE: ColourScale = {
  id: 'access',
  label: 'Bicycle access',
  summary:
    'Legal access as recorded by a source. Contour never infers access from road class or from the presence of a cycle route.',
  bands: [
    {
      key: 'designated',
      label: 'Designated',
      colour: '#1a7f37',
      pattern: null,
      description: 'Explicitly designated for bicycles.',
    },
    {
      key: 'yes',
      label: 'Permitted',
      colour: '#4a9d5f',
      pattern: [8, 2],
      description: 'Bicycles are permitted.',
    },
    {
      key: 'restricted',
      label: 'Restricted',
      colour: '#d1601f',
      pattern: [3, 3],
      description:
        'Access is conditional — permissive, destination-only, dismount or similar. Read the segment detail.',
    },
    {
      key: 'no',
      label: 'Not permitted',
      colour: '#a4232c',
      pattern: [2, 2],
      description: 'A source records that bicycles are not permitted.',
    },
    UNKNOWN_BAND,
  ],
};

export const CYCLE_INFRASTRUCTURE_SCALE: ColourScale = {
  id: 'cycle-infrastructure',
  label: 'Cycle infrastructure',
  summary:
    'Infrastructure recorded alongside or forming the route. “None recorded” and “recorded as absent” are different, and are shown differently.',
  bands: [
    {
      key: 'separated',
      label: 'Separated',
      colour: '#1a7f37',
      pattern: null,
      description: 'Physically separated from the carriageway.',
    },
    {
      key: 'dedicated',
      label: 'Dedicated lane',
      colour: '#4a9d5f',
      pattern: [8, 2],
      description: 'A dedicated cycle lane.',
    },
    {
      key: 'shared',
      label: 'Shared',
      colour: '#e8a33d',
      pattern: [4, 2],
      description: 'Shared use, possibly with pedestrians.',
    },
    {
      key: 'absent',
      label: 'Recorded as none',
      colour: '#6b6f76',
      pattern: [2, 6],
      description:
        'A source explicitly records that there is no cycle infrastructure here. This is a fact, not missing data.',
    },
    UNKNOWN_BAND,
  ],
};

export const CONFIDENCE_SCALE: ColourScale = {
  id: 'confidence',
  label: 'Data confidence',
  summary:
    'How well evidenced a segment is: how many attributes are known, how recent the source is, and whether sources agree. This is a measure of evidence, never of safety.',
  bands: [
    {
      key: 'high',
      label: 'Well evidenced',
      colour: '#1a5f8b',
      pattern: null,
      description: 'Most attributes known, recent source, no disagreement.',
      min: 0.75,
    },
    {
      key: 'medium',
      label: 'Partly evidenced',
      colour: '#5b8db8',
      pattern: [6, 2],
      description: 'Some attributes missing or the source is older.',
      min: 0.4,
      max: 0.75,
    },
    {
      key: 'low',
      label: 'Thinly evidenced',
      colour: '#a8bdd0',
      pattern: [2, 4],
      description:
        'Most attributes are unknown here. Route figures covering this section carry correspondingly little weight.',
      max: 0.4,
    },
    UNKNOWN_BAND,
  ],
};

export const SOURCE_SCALE: ColourScale = {
  id: 'source',
  label: 'Source',
  summary: 'Which publisher supplied the geometry and attributes for each section.',
  bands: [
    {
      key: 'official',
      label: 'Official',
      colour: '#1a5f8b',
      pattern: null,
      description: 'From a government or authority dataset.',
    },
    {
      key: 'openstreetmap',
      label: 'OpenStreetMap',
      colour: '#7cb342',
      pattern: [8, 2],
      description: 'From OpenStreetMap contributors.',
    },
    {
      key: 'imported',
      label: 'Imported',
      colour: '#8a6d3b',
      pattern: [4, 2],
      description: 'From a file you uploaded.',
    },
    {
      key: 'generated',
      label: 'Generated by Contour',
      colour: '#6b4c9a',
      pattern: [3, 3],
      description:
        'Geometry Contour produced with the routing engine. Never presented as an official route.',
    },
    UNKNOWN_BAND,
  ],
};

export const COLOUR_SCALES: Record<ColourModeId, ColourScale> = {
  gradient: GRADIENT_SCALE,
  surface: SURFACE_SCALE,
  access: ACCESS_SCALE,
  'cycle-infrastructure': CYCLE_INFRASTRUCTURE_SCALE,
  confidence: CONFIDENCE_SCALE,
  source: SOURCE_SCALE,
  elevation: {
    id: 'elevation',
    label: 'Elevation',
    summary: 'Height above sea level, from the elevation model named with the route.',
    bands: [
      { key: 'low', label: 'Lower', colour: '#2d5f8b', pattern: null, description: 'Lower ground.' },
      {
        key: 'mid',
        label: 'Middle',
        colour: '#7cb342',
        pattern: [6, 2],
        description: 'Mid elevation.',
      },
      {
        key: 'high',
        label: 'Higher',
        colour: '#a4232c',
        pattern: [2, 4],
        description: 'Higher ground.',
      },
      UNKNOWN_BAND,
    ],
  },
  stage: {
    id: 'stage',
    label: 'Stage',
    summary: 'Which day of the journey each section belongs to.',
    bands: [
      { key: 'odd', label: 'Odd stages', colour: '#1a5f8b', pattern: null, description: 'Stages 1, 3, 5 and so on.' },
      {
        key: 'even',
        label: 'Even stages',
        colour: '#d1601f',
        pattern: [6, 3],
        description: 'Stages 2, 4, 6 and so on.',
      },
      {
        ...UNKNOWN_BAND,
        label: 'Not yet assigned',
        description:
          'This section has not been assigned to a stage yet. It is not a gap in the route.',
      },
    ],
  },
};

export interface MapMode {
  readonly id: MapModeId;
  readonly label: string;
  readonly description: string;
  /** Colour scale applied to the route line in this mode, if any. */
  readonly colourMode: ColourModeId | null;
  /** True when the mode needs a basemap provider to be meaningful. */
  readonly requiresBasemap: boolean;
  /** True when the mode needs terrain data. */
  readonly requiresTerrain: boolean;
  /** True when the mode needs elevation data on the route itself. */
  readonly requiresElevation: boolean;
  /** Rendered as a schematic rather than on the map canvas (§10.4). */
  readonly isSchematic: boolean;
}

export const MAP_MODES: readonly MapMode[] = [
  {
    id: 'standard',
    label: 'Standard',
    description: 'The route over a general-purpose basemap.',
    colourMode: null,
    requiresBasemap: true,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'cycling-network',
    label: 'Cycling network',
    description: 'Signed and designated cycle routes around this route.',
    colourMode: 'cycle-infrastructure',
    requiresBasemap: true,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'topographic',
    label: 'Topographic',
    description: 'Contours and terrain shading, with the route drawn over them.',
    colourMode: null,
    requiresBasemap: true,
    requiresTerrain: true,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'satellite',
    label: 'Satellite',
    description: 'Aerial imagery with place labels.',
    colourMode: null,
    requiresBasemap: true,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'terrain-3d',
    label: '3D terrain',
    description: 'The route draped over terrain, with pitch, bearing and flyover.',
    colourMode: 'gradient',
    requiresBasemap: true,
    requiresTerrain: true,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'slope',
    label: 'Slope',
    description: 'The route coloured and patterned by gradient.',
    colourMode: 'gradient',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: true,
    isSchematic: false,
  },
  {
    id: 'surface',
    label: 'Surface',
    description: 'The route coloured and patterned by recorded surface.',
    colourMode: 'surface',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'access',
    label: 'Access and restrictions',
    description: 'Legal bicycle access as recorded, including where it is unknown.',
    colourMode: 'access',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'road-class',
    label: 'Road classification',
    description:
      'Road class along the route. Used as a labelled proxy for traffic exposure; Contour holds no traffic data.',
    colourMode: 'source',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'provenance',
    label: 'Source and provenance',
    description: 'Which publisher supplied each section.',
    colourMode: 'source',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'confidence',
    label: 'Data confidence',
    description: 'How well evidenced each section is. A measure of evidence, not of safety.',
    colourMode: 'confidence',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: false,
  },
  {
    id: 'topology',
    label: 'Route topology',
    description:
      'A schematic of the journey: stages, climbs, ferries, junctions and services, with distance and ascent between them.',
    colourMode: 'stage',
    requiresBasemap: false,
    requiresTerrain: false,
    requiresElevation: false,
    isSchematic: true,
  },
];

export function mapMode(id: MapModeId): MapMode {
  const found = MAP_MODES.find((mode) => mode.id === id);
  if (!found) throw new Error(`unknown map mode: ${id}`);
  return found;
}

/**
 * Classify a numeric value into a band.
 *
 * Returns the unknown band for null or undefined rather than defaulting to the
 * lowest band, which would render an unsurveyed segment as flat, paved and
 * green — the single most misleading thing this file could do.
 */
export function unknownBand(scale: ColourScale): ScaleBand {
  const band = scale.bands.find((candidate) => candidate.key === 'unknown');
  if (!band) {
    // Every scale must be able to say "not recorded". A scale without an
    // unknown band would force unclassified data into a real band — rendering
    // an unsurveyed lane as flat, paved and green, which is the single most
    // misleading thing this module could do. Failing loudly at the call site
    // beats silently picking a neighbour.
    throw new Error(
      `colour scale "${scale.id}" has no unknown band, so it cannot represent missing data`,
    );
  }
  return band;
}

export function bandFor(scale: ColourScale, value: number | null | undefined): ScaleBand {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return unknownBand(scale);
  }
  for (const band of scale.bands) {
    if (band.key === 'unknown') continue;
    const aboveMin = band.min === undefined || value >= band.min;
    const belowMax = band.max === undefined || value < band.max;
    if (aboveMin && belowMax) return band;
  }
  return unknownBand(scale);
}

/** Classify a categorical value, falling back to unknown for anything unrecognised. */
export function bandForKey(scale: ColourScale, key: string | null | undefined): ScaleBand {
  if (!key) return unknownBand(scale);
  return scale.bands.find((band) => band.key === key) ?? unknownBand(scale);
}
