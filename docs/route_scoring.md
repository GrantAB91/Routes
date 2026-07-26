# Route scoring

How alternatives are ranked, and what a score does not mean.

## Components, not a total

`RouteScore` stores every component with its raw value, its weight and its
weighted contribution. A total alone cannot explain a ranking, and storing only
the total means a weighting change requires re-routing rather than re-scoring.

Components that could not be evaluated are listed separately in
`unscored_components`. A high score computed from four of nine components is not
the same as a high score computed from all nine, and the comparison screen shows
which it is.

## Components

| Component | Source | Notes |
| --- | --- | --- |
| Ascent | Elevation analysis | Unscored where elevation is unknown |
| Sustained gradient | Elevation analysis, per window | Unscored without elevation |
| Surface composition | Segment attributes | Unknown surface is its own share, never folded into paved or unpaved |
| Cycle infrastructure | Segment attributes | "Recorded as none" and "not recorded" score differently |
| Road class exposure | Segment attributes | **A labelled proxy.** Contour holds no traffic data |
| Corridor deviation | Reference line, where one exists | Distance from the official line |
| Directness | Route length against the shortest compliant route | |
| Unknown data share | Segment attributes | Always evaluable |

## What a score is not

A route score measures how well a route matches the preferences a rider stated.
It is **not** a safety rating, and no component is a safety measurement.

Road class is the component most likely to be misread. It is used as a proxy for
traffic exposure because it is the only signal available, and it is labelled a
proxy everywhere it appears — in the API response, in the intent parser's note,
and on the comparison screen. Contour has no observed traffic data of any kind.

## Confidence

Segment confidence is a separate 0–1 measure derived from attribute
completeness, source age and source agreement. It answers "how well evidenced is
this?" and never "how good is this?". A well-surveyed dangerous road scores high
confidence.
