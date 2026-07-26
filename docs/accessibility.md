# Accessibility

Target: **WCAG 2.2 AA**.

## Approach

The rule that shaped most of the interface is §10.7 — colour must never be the
only channel — and it is enforced by the type system rather than by review. A
colour scale band cannot exist without a pattern, a label and a plain-language
description, and the legend, the map layer and the accessible table are all
generated from the same definition. A band cannot appear on the route without
appearing in the legend.

## Specific provisions

| Requirement | How |
| --- | --- |
| Keyboard navigation | Every control is reachable; a skip link jumps to the planner |
| Visible focus | 3px outline with offset, never removed |
| Charts | The elevation profile is SVG, focusable and arrow-key driven, with Enter starting and ending a range selection so drag-to-select has a keyboard equivalent |
| Chart alternatives | Every chart ships a real data table, not an `aria-label` summarising the data away |
| Colour independence | Line pattern, label, description and table position all carry the same information |
| Contrast | Text at 4.5:1 or better; large text and UI at 3:1 |
| Touch targets | Minimum 44px |
| Hover independence | No essential information is hover-only |
| Reduced motion | `prefers-reduced-motion` disables the flyover and all transitions |
| Live regions | Route changes, parse results and profile position announce via `role="status"` |
| Route summaries | Every route, profile, stage plan and warning has a text summary that stands alone |

The topology view is a schematic *and* an ordered list. Screen reader users get
the same sequence in the same order without any of the drawing, because the list
is the primary representation and the diagram is decorative
(`aria-hidden="true"`).

## Testing

- axe-core runs in the Playwright suite.
- Structural invariants are unit-tested: every scale can represent unknown, every
  band carries a non-colour channel, no scale distinguishes two bands by solid
  line alone.

### Manual keyboard test

1. Load the planner. Press Tab once — the skip link must appear and be usable.
2. Tab to the request field, type a request, submit with Enter.
3. Tab through the interpreted-intent table. Every value must show the phrase it
   came from.
4. Tab to the map mode buttons. Arrow keys move between them; the active mode
   announces via the status region.
5. Tab to the elevation profile. Arrow keys move the marker; Shift+Arrow moves in
   larger steps; Home and End jump to the ends.
6. Press Enter to start a range, move, press Enter again. The range statistics
   must announce.
7. Press Escape. The range must clear.
8. Switch to the topology view. Every node must be reachable and announce its
   kind by name, not by symbol.

## Known gaps

- No screen reader testing with NVDA, JAWS or VoiceOver has been performed.
- The map canvas itself is not keyboard-pannable yet; route inspection is done
  through the profile, the topology list and the segment table, all of which are.
