"""How the invented sky reads as an image: colour, size, and the dark it sits on.

Three decisions, and nothing else:

    star_colour(spectral)  -> "#rrggbb"   where on the blue-to-red progression a star sits
    star_radius(magnitude) -> float       how large a dot that brightness deserves
    background()           -> "#rrggbb"   the night the whole thing is drawn against

The renderer calls these once per star and silently falls back to its own defaults if a
call raises or returns something it cannot use. A silent fallback is indistinguishable
from a bad palette in the finished SVG, so every function here is total: it takes any
input at all, including the wrong type, and still returns something drawable.
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------- colour

# The Harvard sequence O B A F G K M runs hot to cool, and colour follows temperature:
# blue-white through white to orange-red. These are pushed a little past the true
# blackbody values -- real starlight is far less saturated than this -- because at a
# radius of two pixels on a dark ground an honest colour is simply not visible. The
# figure has to read as a progression at a glance, so the ends are exaggerated and the
# middle (F, G) kept near white to keep the run monotone rather than rainbow.
_SPECTRAL_COLOURS = {
    "O": "#8ab0ff",  # hottest: hard blue
    "B": "#a9c4ff",  # blue-white
    "A": "#d3e0ff",  # white with a cold cast
    "F": "#f6f4ff",  # neutral white, the hinge of the sequence
    "G": "#fff2d6",  # warm white
    "K": "#ffc978",  # orange
    "M": "#ff9257",  # coolest: orange-red
}

# For a class letter that is not in the sequence at all. Deliberately a plain, slightly
# cool white: it stays legible without pretending to be a temperature it never claimed.
_UNKNOWN_COLOUR = "#dfe6f5"

# Deep night, not black. Pure #000000 gives the faint stars nothing to sit against and
# flattens the whole plate; a dark blue-violet keeps a sense of depth behind them and
# leaves room for the M stars to read as genuinely warm by contrast.
_BACKGROUND = "#0b1024"


def star_colour(spectral: str) -> str:
    """Return the colour for a spectral class as "#rrggbb".

    Forgiving about its input on purpose: real class strings arrive as "G", "g", " K ",
    or a full type like "G2V", and all of them mean the same row of the table.
    """
    if not isinstance(spectral, str):
        return _UNKNOWN_COLOUR
    letter = spectral.strip()[:1].upper()
    return _SPECTRAL_COLOURS.get(letter, _UNKNOWN_COLOUR)


# --------------------------------------------------------------------------- radius

# The magnitude band the curve is shaped for: exactly the range the data tasks are told
# to write, -1 (very bright) to 5 (faint). Anchoring wider than that -- padding out to
# the renderer's own -2..8 clamp, say -- is a tempting bit of caution that quietly ruins
# the picture: it spends most of the curve on magnitudes nobody writes and squeezes the
# real stars into the middle, so the brightest star in the sky comes out mid-sized and
# the plate reads flat. Anything outside the band clamps to an end, which is all the
# robustness the extremes actually need.
_MAG_BRIGHTEST = -1.0
_MAG_FAINTEST = 5.0

# Chosen so the ends of the band land inside the sizes the task asks for -- brightest
# 7-9 px, faintest 1-2 px -- rather than exactly on their edges.
_R_MAX = 8.6
_R_MIN = 1.3

# Magnitude is already logarithmic in flux, so a straight line from it to radius is not
# the naive choice one might expect -- but it still reads badly, because the eye judges
# a dot by its area. Linear radius makes the middle of the range bunch up into one
# indistinguishable size. Gamma > 1 pulls the faint half down hard and leaves the bright
# few standing clear, which is what gives a star chart its hierarchy.
_GAMMA = 1.6

# What the renderer will accept. Clamping to it here rather than letting it reject the
# value keeps a freak input from quietly switching the whole map to fallback sizing.
_R_FLOOR = 0.1
_R_CEILING = 40.0


def star_radius(magnitude: float) -> float:
    """Return the drawing radius in pixels for a magnitude.

    Bright stars land near 7-9 px, faint ones near 1-2 px, along a curve rather than a
    line. Anything unreadable as a number is drawn at middling size instead of raising:
    a star of unknown brightness is still a star.
    """
    try:
        mag = float(magnitude)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is not hypothetical: JSON admits integers of unbounded size, and
        # one too large for a float reaches here before any arithmetic can clamp it.
        return _midpoint_radius()
    if math.isnan(mag) or math.isinf(mag):
        return _midpoint_radius()

    # Normalise so 1.0 is the brightest anchor and 0.0 the faintest, then clamp: beyond
    # the anchors the curve is meaningless, and a negative base under a fractional
    # exponent is not a real number at all.
    t = (_MAG_FAINTEST - mag) / (_MAG_FAINTEST - _MAG_BRIGHTEST)
    t = max(0.0, min(1.0, t))

    radius = _R_MIN + (_R_MAX - _R_MIN) * (t**_GAMMA)
    return max(_R_FLOOR, min(_R_CEILING, radius))


def _midpoint_radius() -> float:
    """The size for a star whose magnitude could not be read: mid-scale, unremarkable."""
    return star_radius((_MAG_BRIGHTEST + _MAG_FAINTEST) / 2.0)


# ----------------------------------------------------------------------- background


def background() -> str:
    """Return the colour of the night the map is drawn on, as "#rrggbb"."""
    return _BACKGROUND
