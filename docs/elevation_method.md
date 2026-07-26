# Elevation method

Everything Contour reports about height, gradient and climbing, and how it is
produced. This document exists because "1,400 m of ascent" is not a measurement
— it is the output of a chain of decisions, and two tools disagreeing by 40% on
the same GPX file are usually disagreeing about these decisions rather than
about the terrain.

Implementation: `apps/api/src/contour_api/analysis/elevation.py` and
`analysis/climbs.py`. Tests: `tests/unit/test_elevation.py`,
`tests/unit/test_climbs.py`.

## 1. Source

Contour reads elevation through an `ElevationProvider`, with two implementations:

- **Valhalla `/height`** — the routing engine's own elevation service, which
  serves whatever DEM was built into its tiles.
- **Local raster** — GeoTIFF or HGT tiles read directly.

The dataset in use is recorded on every sample and shown with every figure.
`CONTOUR_ELEVATION_DATASET_ID` must name it exactly (for example
`Copernicus DEM GLO-30 (2021 release)`); the raster provider refuses to start
without it, because an elevation figure whose source cannot be named cannot have
its accuracy stated.

**Current state: no elevation provider is configured in this deployment.** The
DEM host is unreachable under the network policy, so every elevation figure
reports as unknown rather than being estimated. This is visible on
`/health/components` and on the Coverage screen.

## 2. Horizontal resolution and sampling

The sampling interval is derived from the source's native resolution, never
chosen freely:

```
sample_interval = max(requested_interval, source_resolution)
```

Asking a 30 m DEM for 10 m samples does not produce 10 m of detail. It produces
interpolation presented as measurement, which is exactly what §11.3 forbids. The
interval is raised to the source resolution and the corrected value is returned
with the result.

## 3. Vertical accuracy

Contour does not publish an accuracy figure for a dataset unless the publisher
does. Where the publisher states one it is recorded in the source registry and
shown alongside elevation figures. Where they do not, no figure is invented.

This matters for how results are displayed: a 30 m DEM with metre-level vertical
accuracy cannot support elevation quoted to a decimal place, so Contour rounds
displayed elevation to whole metres.

There is one deliberate exception, in the opposite direction. Valhalla's
`/height` endpoint defaults to integer precision, and its own documentation
notes this produces "stair step" changes along nearly flat roads. Those steps are
counted as real climbing by any cumulative-ascent calculation, systematically
inflating the ascent of flat routes. Contour therefore requests
`height_precision=2` for *computation*, to remove a quantisation artefact — not
because the underlying data supports centimetres. Display precision is governed
by the paragraph above.

## 4. Noise filtering

**Separate from smoothing, and applied first.** §11.2 lists them as different
items, and building both showed why.

Smoothing is a weighted mean, and a mean is not robust: a single sample 390 m out
— the signature of a DEM seam, a bridge deck, or a void fill — is not removed by
averaging, it is spread across the window. In testing, one such sample on an
otherwise flat 150 m of road still produced **235 m of phantom ascent** after
smoothing, and became the route's highest point.

Contour therefore applies a median filter first. A sample is judged implausible
when it differs from the median of its neighbourhood by more than the steepest
real terrain could account for over that distance:

```
plausible_deviation = max_plausible_grade_percent / 100 × (window / 2)
```

Implausible samples are replaced by the local median, and the number of
replacements is reported on the profile. With this in place the same test case
yields the true 5 m of ascent with exactly one sample replaced.

The centre sample is included in its own median window. Excluding it lets a spike
dominate the median of each *neighbouring* window, so the filter replaces the two
good samples either side and leaves the artefact in place — which is what it did
before this was fixed.

## 5. Smoothing

Distance-weighted moving average over `smoothing_window_m`, defaulting to three
sample intervals, with triangular weighting.

Averaging by distance rather than by sample count matters because route samples
are not evenly spaced: they cluster at bends, where the geometry has more
vertices. A count-based window smooths hard through hairpins and barely at all on
straights, producing phantom climbing exactly where mountain roads switchback.

Triangular weighting is used rather than a boxcar so the filter does not shift
peaks.

## 6. Missing data

**Gaps are never interpolated across.** A run of samples with no coverage splits
the profile into separate spans, and nothing is computed across the hole:

- ascent and descent accumulate within each span and are summed;
- net elevation change across a gap is reported as **unknown**, because the
  elevation either side says nothing about what happened between;
- the unmeasured distance is reported next to the ascent figure, so the number is
  never read as covering the whole route;
- a route with no coverage at all reports `None` for every derived figure, not
  zero. Zero ascent is a claim about the terrain.

## 7. Cumulative ascent

Hysteresis against a minimum-gain threshold, defaulting to 3 m.

A run of rises only becomes ascent once it exceeds the threshold, so wobble below
the model's own vertical accuracy is not counted. Without this the total grows
steadily as sampling tightens — the classic symptom of measuring noise rather
than terrain. Two properties are tested directly:

- 200 samples of ±1 m noise on a flat road sum to over 40 m of phantom climbing
  naively, and report **0** here;
- the same hill sampled at 30 m and at 10 m agrees within 10%, so two tools
  cannot disagree merely by sampling differently.

## 8. Gradient

Computed over explicit windows, defaulting to **25 m, 100 m, 500 m and 1,000 m**.
A gradient figure is meaningless without the distance it was measured over: 20%
over 25 m is a driveway lip, 20% over 1 km is a mountain pass.

Windows shorter than the sampling interval are **dropped, not interpolated**. On
a 30 m DEM the 25 m window is unsupportable and is not offered; `dropped_windows()`
reports which were removed so the omission is visible rather than silent.

Gradients beyond `max_plausible_grade_percent` (default 40%) are rejected as
artefacts and counted. A route with many rejections is one whose elevation data
should be distrusted, which is worth surfacing.

The headline "maximum gradient" is the shortest supported window's figure, and is
always displayed with that window.

## 9. Climb detection

A climb is not a property of terrain. It is a decision about which rises are
worth naming, governed by three parameters that are stored with every detected
climb:

| Parameter | Default | Why |
| --- | --- | --- |
| `min_gain_m` | 30 m | Below this it is rolling terrain |
| `min_length_m` | 300 m | Below this it is a ramp |
| `min_average_grade_percent` | 3% | The whole Wild Atlantic Way averages under 1%; without this every route has one enormous "climb" |
| `max_internal_descent_m` | 20 m | Descent tolerated *inside* one climb |

The last is the one that matters on Irish coastal roads, where a col is routinely
interrupted by 10–20 m dips. Splitting on every dip turns one climb into six
meaningless fragments; never splitting merges a whole day into one. Climbs found
under different parameters are not comparable, which is why the parameters travel
with the result.

Detection runs on the filtered, smoothed profile. On raw samples it finds hundreds
of climbs made of DEM noise. It never spans a coverage gap, since the elevation
change inside a hole is unknown rather than a climb.

A climb is trimmed to start at the *last* lowest sample before its summit. Taking
the first leaves a flat approach inside the climb: 1 km of flat before 1 km at 6%
averages 2.99%, which falls under the 3% minimum and makes the climb vanish
entirely rather than merely read shallow.

## 10. Route snapping

Elevation is sampled along the geometry Contour actually routes, not along the
original source line. Where a route has been map-matched, the matched geometry is
stored separately and the original is never overwritten, so an elevation profile
can be recomputed against either.

## 11. Known limitations

- A 30 m DEM cannot resolve short steep ramps. A 15% pitch over 20 m is invisible
  at that resolution, and Contour cannot report what the model cannot see.
- Bridges and tunnels take the terrain surface's elevation, not the deck's, since
  a DEM models ground. This produces spurious dips and climbs at crossings; the
  median filter removes the worst of them, and the residual is a known error.
- Vegetation and buildings raise surface models. Where the dataset is a surface
  model rather than a terrain model, elevations along tree-lined lanes read high.
- Contour holds no barometric data and does not use recorded elevation from
  uploaded tracks to correct the DEM. Imported elevation is preserved and shown
  as the file's own, separately from Contour's analysis.

## 12. Attribution

Every elevation figure carries the dataset that produced it, and the dataset's
required attribution appears on the route detail screen and in every export
manifest. Where a dataset's licence terms have not been verified, export of
derived elevation figures is refused — see
[licensing_and_attribution.md](licensing_and_attribution.md).
