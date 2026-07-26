'use client';

/**
 * Route topology view (§10.4).
 *
 * A schematic, not a map. The point is to answer questions a map answers badly:
 * how far between overnight stops, which climbs fall in which day, where the
 * ferries are, and where the route branches. Geography is deliberately
 * discarded — nodes are evenly spaced regardless of real distance — so that a
 * 3 km gap and a 90 km gap get the same visual room, and the distances between
 * them are read from labels rather than estimated from pixel lengths.
 *
 * It is a list in the accessibility tree, because that is what it is: an ordered
 * sequence of things you pass. Screen reader users get the same content in the
 * same order without any of the drawing.
 */

import { useId } from 'react';

export type TopologyNodeKind =
  | 'start'
  | 'finish'
  | 'stage-end'
  | 'climb'
  | 'ferry'
  | 'branch'
  | 'junction'
  | 'service'
  | 'overnight'
  | 'warning'
  | 'surface-change'
  | 'alternative';

export interface TopologyNode {
  id: string;
  kind: TopologyNodeKind;
  label: string;
  distanceM: number;
  /** Ascent from the previous node, null where elevation is unknown. */
  ascentFromPreviousM?: number | null;
  detail?: string;
  warning?: string;
}

const SYMBOL: Record<TopologyNodeKind, { glyph: string; colour: string; name: string }> = {
  start: { glyph: '▶', colour: '#1a7f37', name: 'Start' },
  finish: { glyph: '■', colour: '#1a3d5c', name: 'Finish' },
  'stage-end': { glyph: '◆', colour: '#1a5f8b', name: 'Stage end' },
  overnight: { glyph: '◆', colour: '#1a5f8b', name: 'Overnight stop' },
  climb: { glyph: '▲', colour: '#a4232c', name: 'Climb' },
  ferry: { glyph: '≈', colour: '#0f6f8c', name: 'Ferry crossing' },
  branch: { glyph: '⑂', colour: '#6b4c9a', name: 'Route branch' },
  junction: { glyph: '✚', colour: '#6b6f76', name: 'Junction' },
  service: { glyph: '●', colour: '#7cb342', name: 'Service' },
  warning: { glyph: '!', colour: '#d1601f', name: 'Warning' },
  'surface-change': { glyph: '▬', colour: '#8a6d3b', name: 'Surface change' },
  alternative: { glyph: '⑃', colour: '#6b4c9a', name: 'Alternative route junction' },
};

export interface RouteTopologyProps {
  nodes: readonly TopologyNode[];
  selectedNodeId?: string | null;
  onSelectNode?: (id: string) => void;
}

export function RouteTopology({ nodes, selectedNodeId, onSelectNode }: RouteTopologyProps) {
  const headingId = useId();

  if (nodes.length === 0) {
    return (
      <section aria-labelledby={headingId} className="topology topology--empty">
        <h2 id={headingId}>Route topology</h2>
        <p>
          Nothing to show yet. The topology view lists stages, climbs, ferries and
          services once a route has been generated.
        </p>
      </section>
    );
  }

  const rowHeight = 64;
  const width = 720;
  const height = nodes.length * rowHeight + 32;
  const spineX = 120;

  return (
    <section aria-labelledby={headingId} className="topology">
      <h2 id={headingId}>Route topology</h2>
      <p className="topology__note">
        A schematic of what you pass, in order. Spacing is even and does not represent
        distance — distances are labelled between nodes.
      </p>

      <div className="topology__layout">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="topology__diagram"
          aria-hidden="true"
          focusable="false"
        >
          <line
            x1={spineX}
            x2={spineX}
            y1={24}
            y2={height - 24}
            stroke="#c7ccd1"
            strokeWidth={3}
          />
          {nodes.map((node, index) => {
            const y = 24 + index * rowHeight;
            const symbol = SYMBOL[node.kind];
            const previous = index > 0 ? nodes[index - 1] : undefined;
            const legDistance = previous ? node.distanceM - previous.distanceM : null;

            return (
              <g key={node.id}>
                {legDistance !== null ? (
                  <text x={spineX - 12} y={y - rowHeight / 2 + 4} textAnchor="end" fontSize={12} fill="#5b6167">
                    {(legDistance / 1000).toFixed(1)} km
                    {node.ascentFromPreviousM == null
                      ? ' · ascent unknown'
                      : ` · ${Math.round(node.ascentFromPreviousM)} m up`}
                  </text>
                ) : null}
                <circle
                  cx={spineX}
                  cy={y}
                  r={node.id === selectedNodeId ? 14 : 11}
                  fill="#fff"
                  stroke={symbol.colour}
                  strokeWidth={node.id === selectedNodeId ? 4 : 2}
                />
                <text
                  x={spineX}
                  y={y + 4}
                  textAnchor="middle"
                  fontSize={12}
                  fill={symbol.colour}
                >
                  {symbol.glyph}
                </text>
                <text x={spineX + 22} y={y + 4} fontSize={14} fill="#111">
                  {node.label}
                </text>
              </g>
            );
          })}
        </svg>

        {/* The same sequence as a real list. Every node is reachable by keyboard
            and carries its symbol's name in text, so the diagram's glyphs never
            carry meaning on their own (§10.7). */}
        <ol className="topology__list">
          {nodes.map((node, index) => {
            const previous = index > 0 ? nodes[index - 1] : undefined;
            const legDistance = previous ? node.distanceM - previous.distanceM : null;
            const symbol = SYMBOL[node.kind];

            return (
              <li key={node.id}>
                <button
                  type="button"
                  className="topology__node"
                  aria-current={node.id === selectedNodeId ? 'true' : undefined}
                  onClick={() => onSelectNode?.(node.id)}
                >
                  <span className="topology__kind" style={{ color: symbol.colour }}>
                    {symbol.name}
                  </span>
                  <span className="topology__label">{node.label}</span>
                  <span className="topology__distance">
                    {(node.distanceM / 1000).toFixed(1)} km
                  </span>
                  {legDistance !== null ? (
                    <span className="topology__leg">
                      {(legDistance / 1000).toFixed(1)} km from previous,{' '}
                      {node.ascentFromPreviousM == null
                        ? 'ascent unknown'
                        : `${Math.round(node.ascentFromPreviousM)} m of ascent`}
                    </span>
                  ) : null}
                  {node.detail ? <span className="topology__detail">{node.detail}</span> : null}
                  {node.warning ? (
                    <span className="topology__warning" role="note">
                      {node.warning}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
