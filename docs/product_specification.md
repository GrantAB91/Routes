# Product specification

## What Contour is

A cycling route planner whose distinguishing property is its handling of missing
data. Most planners fill gaps silently — an untagged lane becomes "paved", an
unsurveyed road becomes "legal", a hole in the elevation model becomes a flat
section — and produce something that looks authoritative and is partly invented.

Contour keeps unknown as a value, reports it wherever a figure depends on it, and
refuses to present a requirement as met when the data needed to check it does not
exist.

## Who it serves

**A new rider** who wants a useful route immediately: one field to describe the
journey, clearly different options, and plain language throughout. Nothing they
need is behind an expert toggle.

**An expert rider** who wants control: hard constraints separated from
preferences, per-segment provenance, gradient measured over stated windows,
alternatives quantified against each other, and a feasibility verdict that
distinguishes "proven compliant" from "not disproved".

Simple mode hides complexity without removing correctness. Expert mode exposes
constraints, scoring, provenance, elevation method and alternative comparison.

## The shortest path (§3.1)

1. Describe or select the journey
2. Review clearly different route choices
3. Inspect and edit the chosen route
4. Divide it into stages
5. Save, share or export

The map is the working surface throughout. Route creation is never buried in a
modal.

## What every route must say

- Why it was selected
- What information was used
- What remains unknown
- Which constraints could not be met, and where

## What Contour will not say

- That a route is **safe**. It reports observable, sourced attributes — road
  class, speed limit, cycle infrastructure, surface, access, lighting, shoulder,
  gradient — and nothing more.
- That it knows **traffic**. It holds no traffic data. Road class is used as a
  labelled proxy and reported as one.
- That its coverage is **complete**. A route absent from the catalogue is absent
  because no connected source published it.
- That an elevation figure is **certain**. Every figure carries its dataset, its
  method and its coverage.

## Delivered

See the table in the [README](../README.md). The routing, elevation, constraint,
staging, import/export, licensing and interface layers are built and tested. The
Wild Atlantic Way reference project is blocked on source data, and the state of
each of its twenty acceptance steps is recorded in
[Wild_Atlantic_Way_acceptance.md](Wild_Atlantic_Way_acceptance.md).
