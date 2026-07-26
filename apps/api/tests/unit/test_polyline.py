"""Polyline decoding tests.

Valhalla ships an encoder in its documentation but no decoded test vector, so
correctness is established by round-tripping through a reference encoder written
directly from the documented algorithm
(``/opt/contour/valhalla-src/docs/docs/api/decoding.md``). If the decoder and
that independent encoder agree across random coordinates, the decoder implements
the documented format.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from contour_api.providers.routing import LatLon
from contour_api.providers.valhalla import decode_polyline5, decode_polyline6


def encode_polyline(coordinates: list[LatLon], precision: int = 6) -> str:
    """Reference encoder, written from the documented algorithm."""
    factor = 10**precision
    output: list[str] = []
    prev_lat = 0
    prev_lon = 0

    for point in coordinates:
        lat = round(point.lat * factor)
        lon = round(point.lon * factor)
        for delta in (lat - prev_lat, lon - prev_lon):
            value = ~(delta << 1) if delta < 0 else (delta << 1)
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
        prev_lat, prev_lon = lat, lon

    return "".join(output)


coordinate = st.builds(
    LatLon,
    lat=st.floats(min_value=-89.9, max_value=89.9, allow_nan=False, allow_infinity=False),
    lon=st.floats(min_value=-179.9, max_value=179.9, allow_nan=False, allow_infinity=False),
)


@given(st.lists(coordinate, min_size=1, max_size=60))
def test_decode_inverts_encode_at_six_digits(points: list[LatLon]) -> None:
    decoded = decode_polyline6(encode_polyline(points, 6))

    assert len(decoded) == len(points)
    for original, result in zip(points, decoded, strict=True):
        # Six decimal digits is roughly 0.1 m; the tolerance is one unit in the
        # last encoded place, which is all the format preserves.
        assert result.lat == pytest.approx(original.lat, abs=1e-6)
        assert result.lon == pytest.approx(original.lon, abs=1e-6)


@given(st.lists(coordinate, min_size=1, max_size=40))
def test_decode_inverts_encode_at_five_digits(points: list[LatLon]) -> None:
    decoded = decode_polyline5(encode_polyline(points, 5))

    for original, result in zip(points, decoded, strict=True):
        assert result.lat == pytest.approx(original.lat, abs=1e-5)
        assert result.lon == pytest.approx(original.lon, abs=1e-5)


def test_wrong_precision_is_rejected_rather_than_silently_misplaced() -> None:
    """The failure upstream warns about, and Contour's guard against it.

    Valhalla's documentation notes that decoding its six-digit shapes with the
    five-digit algorithm places coordinates incorrectly, "commonly, in the
    middle of an ocean". Nothing in the polyline format detects this: the
    decode succeeds and returns plausible-looking numbers ten times too large.

    Contour catches it because LatLon validates its range on construction, so
    the mistake surfaces as an error at the point of decoding instead of as a
    route through the Atlantic. This test pins that guard: if the range check is
    ever relaxed, the silent-misplacement bug comes back.
    """
    points = [LatLon(lat=53.2707, lon=-9.0568), LatLon(lat=53.3707, lon=-9.1568)]
    encoded = encode_polyline(points, 6)

    correct = decode_polyline6(encoded)
    assert correct[0].lat == pytest.approx(53.2707, abs=1e-6)
    assert correct[0].lon == pytest.approx(-9.0568, abs=1e-6)

    with pytest.raises(ValueError, match="latitude out of range"):
        decode_polyline5(encoded)


def test_empty_string_decodes_to_no_points() -> None:
    assert decode_polyline6("") == []


def test_truncated_polyline_is_rejected() -> None:
    """A cut-off shape must fail loudly rather than return a short route."""
    encoded = encode_polyline([LatLon(lat=53.27, lon=-9.05)], 6)

    with pytest.raises(ValueError, match="truncated"):
        decode_polyline6(encoded[:-1])
