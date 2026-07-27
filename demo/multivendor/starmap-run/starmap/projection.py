"""Stereographic projection used by the shared star map."""

from __future__ import annotations

import math


_SOUTHERN_VISIBLE_LIMIT = -60.0


def project(ra_deg: float, dec_deg: float, width: int, height: int) -> tuple[float, float]:
    """Map a point on the celestial sphere to pixel coordinates."""
    width_f = max(0.0, float(width))
    height_f = max(0.0, float(height))
    centre_x = width_f / 2.0
    centre_y = height_f / 2.0

    try:
        ra = math.radians(float(ra_deg) % 360.0)
        dec = max(-90.0, min(90.0, float(dec_deg)))
    except (TypeError, ValueError, OverflowError):
        return centre_x, centre_y

    if not math.isfinite(ra) or not math.isfinite(dec) or dec == 90.0:
        return centre_x, centre_y

    # The constellation data contract makes -60 degrees the southern edge of
    # the visible sky.  tan(colatitude / 2) is the north-pole stereographic
    # radius; normalising by the radius at that edge fits its circle to the
    # shorter canvas dimension.
    edge_radius = math.tan(math.radians((90.0 - _SOUTHERN_VISIBLE_LIMIT) / 2.0))
    radius = math.tan(math.radians((90.0 - dec) / 2.0))
    radius = min(radius, edge_radius)
    pixels = radius * (min(width_f, height_f) / 2.0) / edge_radius

    return (
        centre_x + pixels * math.sin(ra),
        centre_y - pixels * math.cos(ra),
    )
