'use client';

/**
 * Legend for the active colour mode (§10.7).
 *
 * Generated from the same scale definition the map layer uses, so a band can
 * never appear on the route without appearing here. Each entry shows the
 * colour, the pattern, the label and the plain-language description — four
 * channels, of which colour is only one.
 */

import type { ColourScale, ScaleBand } from '@/lib/map-modes';

function PatternSwatch({ band }: { band: ScaleBand }) {
  const dash = band.pattern ? band.pattern.join(' ') : undefined;
  return (
    <svg width={44} height={12} aria-hidden="true" focusable="false">
      <line
        x1={2}
        y1={6}
        x2={42}
        y2={6}
        stroke={band.colour}
        strokeWidth={4}
        strokeDasharray={dash}
        strokeLinecap="round"
      />
    </svg>
  );
}

export function MapLegend({
  scale,
  distanceByBand,
  totalDistanceM,
}: {
  scale: ColourScale;
  /** Metres of route in each band, keyed by band key. Drives the table. */
  distanceByBand?: Record<string, number>;
  totalDistanceM?: number;
}) {
  return (
    <section className="legend" aria-label={`${scale.label} legend`}>
      <h3 className="legend__title">{scale.label}</h3>
      <p className="legend__summary">{scale.summary}</p>

      {/* A table rather than a list, because with distances it is tabular data
          and §10.7 requires the information to be available without colour. */}
      <table className="legend__table">
        <caption className="visually-hidden">
          {scale.label} bands, with the pattern used for each and how much of the
          route falls in it.
        </caption>
        <thead>
          <tr>
            <th scope="col">Line</th>
            <th scope="col">Band</th>
            <th scope="col">Meaning</th>
            {distanceByBand ? <th scope="col">Distance</th> : null}
          </tr>
        </thead>
        <tbody>
          {scale.bands.map((band) => {
            const metres = distanceByBand?.[band.key];
            const share =
              metres !== undefined && totalDistanceM
                ? Math.round((metres / totalDistanceM) * 100)
                : null;
            return (
              <tr key={band.key} data-band={band.key}>
                <td>
                  <PatternSwatch band={band} />
                </td>
                <th scope="row">{band.label}</th>
                <td>{band.description}</td>
                {distanceByBand ? (
                  <td>
                    {metres === undefined
                      ? '—'
                      : `${(metres / 1000).toFixed(1)} km${share !== null ? ` (${share}%)` : ''}`}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
