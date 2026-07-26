"""Render the shared sky to SVG, deterministically, from whatever data exists.

This renderer is not part of the agents' work. It is the fixed instrument they build
against, for one reason: the point of the artefact is that **every state of it can be
re-rendered**, so a time-lapse can be reconstructed from the git history rather than
staged. If a renderer could fail, the time-lapse would have holes exactly where the
interesting moments are -- the half-finished commits.

So it never fails. A missing directory, an unreadable file, a constellation with a line
pointing at a star that does not exist: each is skipped, counted, and noted in the SVG
itself. The image always comes out.

The sky is invented. Nothing here claims to be astronomy -- these are constellations the
agents made up, and the file format only borrows right ascension and declination as a
convenient way to place a point on a sphere.

Determinism matters as much as robustness: same input, byte-identical output. Files are
sorted, stars are sorted, floats are formatted to a fixed precision, and nothing reads
the clock. Two renders of the same commit must not differ, or the time-lapse would
flicker.

Agents may extend it by dropping in any of three optional modules, each picked up if it
imports and ignored if it does not:

    starmap/projection.py   project(ra_deg, dec_deg, width, height) -> (x, y)
    starmap/style.py        star_colour(spectral) -> "#rrggbb"
                            star_radius(magnitude) -> float
                            background() -> "#rrggbb"
    starmap/legend.py       legend_svg(width, height, constellations) -> str
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WIDTH = 1600
HEIGHT = 900

# Used when style.py is absent or unusable. Rough spectral-class colours; the point is
# that the fallback looks deliberate rather than broken.
FALLBACK_COLOURS = {
    "O": "#9bb0ff",
    "B": "#aabfff",
    "A": "#cad7ff",
    "F": "#f8f7ff",
    "G": "#fff4ea",
    "K": "#ffd2a1",
    "M": "#ffcc6f",
}
FALLBACK_BACKGROUND = "#080b1a"


@dataclass
class Star:
    id: str
    ra: float
    dec: float
    mag: float
    spectral: str


@dataclass
class Constellation:
    slug: str
    name: str
    abbr: str
    stars: list[Star] = field(default_factory=list)
    lines: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Report:
    """What the renderer had to ignore. Written into the SVG so it is never silent."""

    files_seen: int = 0
    constellations: int = 0
    stars: int = 0
    segments: int = 0
    skipped_files: list[str] = field(default_factory=list)
    dropped_segments: int = 0
    modules: dict[str, str] = field(default_factory=dict)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return None if math.isnan(result) or math.isinf(result) else result


def parse_constellation(path: Path) -> Constellation | None:
    """Turn one data file into a constellation, or None if it cannot be trusted.

    Deliberately forgiving about what it accepts and strict about what it keeps: a file
    written by an agent at low effort should contribute what it got right rather than
    take the whole render down.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    stars: list[Star] = []
    seen: set[str] = set()
    for entry in raw.get("stars") or []:
        if not isinstance(entry, dict):
            continue
        star_id = entry.get("id")
        ra, dec = _num(entry.get("ra")), _num(entry.get("dec"))
        if not isinstance(star_id, str) or star_id in seen or ra is None or dec is None:
            continue
        if not (0.0 <= ra <= 360.0) or not (-90.0 <= dec <= 90.0):
            continue
        mag = _num(entry.get("mag"))
        spectral = entry.get("spectral")
        stars.append(
            Star(
                id=star_id,
                ra=ra,
                dec=dec,
                mag=3.0 if mag is None else max(-2.0, min(8.0, mag)),
                spectral=spectral.strip()[:1].upper()
                if isinstance(spectral, str) and spectral.strip()
                else "G",
            )
        )
        seen.add(star_id)

    if not stars:
        return None

    lines: list[tuple[str, str]] = []
    for segment in raw.get("lines") or []:
        if (
            isinstance(segment, (list, tuple))
            and len(segment) == 2
            and all(isinstance(end, str) for end in segment)
        ):
            lines.append((segment[0], segment[1]))

    abbr = raw.get("abbr")
    return Constellation(
        slug=path.stem,
        name=name.strip(),
        abbr=abbr.strip()[:4] if isinstance(abbr, str) and abbr.strip() else name.strip()[:3],
        stars=sorted(stars, key=lambda s: s.id),
        lines=lines,
    )


def load_constellations(data_dir: Path, report: Report) -> list[Constellation]:
    if not data_dir.is_dir():
        return []
    found = []
    for path in sorted(data_dir.glob("*.json")):
        report.files_seen += 1
        parsed = parse_constellation(path)
        if parsed is None:
            report.skipped_files.append(path.name)
        else:
            found.append(parsed)
    return found


# ------------------------------------------------------------------ optional modules


def _load_projection(root: Path, report: Report):
    fallback_note = "fallback (equirectangular)"

    def fallback(ra: float, dec: float, width: int, height: int) -> tuple[float, float]:
        return (ra / 360.0) * width, (0.5 - dec / 180.0) * height

    module = _import_optional(root, "projection", report)
    if module is None or not hasattr(module, "project"):
        report.modules["projection"] = fallback_note
        return fallback

    def wrapped(ra: float, dec: float, width: int, height: int) -> tuple[float, float]:
        try:
            x, y = module.project(ra, dec, width, height)
            fx, fy = _num(x), _num(y)
            if fx is None or fy is None:
                return fallback(ra, dec, width, height)
            return fx, fy
        except Exception:
            return fallback(ra, dec, width, height)

    report.modules["projection"] = "starmap/projection.py"
    return wrapped


def _load_style(root: Path, report: Report):
    module = _import_optional(root, "style", report)

    def colour(spectral: str) -> str:
        if module is not None and hasattr(module, "star_colour"):
            try:
                value = module.star_colour(spectral)
                if isinstance(value, str) and value.startswith("#") and len(value) in (4, 7):
                    return value
            except Exception:
                pass
        return FALLBACK_COLOURS.get(spectral, "#ffffff")

    def radius(magnitude: float) -> float:
        if module is not None and hasattr(module, "star_radius"):
            try:
                value = _num(module.star_radius(magnitude))
                if value is not None and 0.1 <= value <= 40.0:
                    return value
            except Exception:
                pass
        return max(1.0, 7.0 - magnitude * 1.2)

    def background() -> str:
        if module is not None and hasattr(module, "background"):
            try:
                value = module.background()
                if isinstance(value, str) and value.startswith("#") and len(value) in (4, 7):
                    return value
            except Exception:
                pass
        return FALLBACK_BACKGROUND

    report.modules["style"] = "starmap/style.py" if module is not None else "fallback"
    return colour, radius, background


def _load_legend(root: Path, report: Report):
    module = _import_optional(root, "legend", report)

    def render(width: int, height: int, constellations: list[Constellation]) -> str:
        if module is not None and hasattr(module, "legend_svg"):
            try:
                value = module.legend_svg(width, height, constellations)
                if isinstance(value, str):
                    return value
            except Exception:
                pass
        return ""

    report.modules["legend"] = "starmap/legend.py" if module is not None else "fallback (none)"
    return render


def _import_optional(root: Path, name: str, report: Report):
    """Import `starmap/<name>.py` from the workspace, or return None.

    Imported by path rather than by package, because the workspace is not installed and
    must not need to be.
    """
    import importlib.util

    path = root / "starmap" / f"{name}.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(f"_starmap_{name}", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        report.skipped_files.append(f"starmap/{name}.py (did not import)")
        return None


# ------------------------------------------------------------------------ the drawing


def _f(value: float) -> str:
    """Fixed formatting, so two renders of one commit are byte-identical."""
    return f"{value:.2f}"


MARGIN = 70


def bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _fit_transform(points: list[tuple[float, float]], viewport=None):
    """Scale and centre the projected drawing so it uses the canvas.

    Framing is the renderer's job, not the projection's. The projection module decides
    the *shape* -- which point goes where relative to the others -- and this preserves
    that exactly: one uniform scale and one translation, no distortion, no reordering.
    What it fixes is only how much of the canvas the result occupies, which is a
    presentation concern and not something the agents' mathematics should have to solve.

    `viewport` pins the framing to a fixed box instead of measuring the current points.
    The time-lapse uses that so the camera holds still while the sky fills up, rather
    than zooming out on every new constellation.
    """
    box = viewport or bounds(points)
    if box is None:
        return lambda point: point

    min_x, min_y, max_x, max_y = box
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    scale = min((WIDTH - 2 * MARGIN) / span_x, (HEIGHT - 2 * MARGIN) / span_y)
    offset_x = (WIDTH - span_x * scale) / 2 - min_x * scale
    offset_y = (HEIGHT - span_y * scale) / 2 - min_y * scale

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        return point[0] * scale + offset_x, point[1] * scale + offset_y

    return transform


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_svg(
    root: Path, caption: str = "", viewport: tuple[float, float, float, float] | None = None
) -> tuple[str, Report]:
    report = Report()
    constellations = load_constellations(root / "data" / "constellations", report)
    project = _load_projection(root, report)
    colour, radius, background = _load_style(root, report)
    legend = _load_legend(root, report)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="A sky invented by three agents">'
    )
    parts.append(f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{background()}"/>')

    ordered = sorted(constellations, key=lambda c: c.slug)
    projected = [
        (c, {star.id: project(star.ra, star.dec, WIDTH, HEIGHT) for star in c.stars})
        for c in ordered
    ]
    fit = _fit_transform([p for _, points in projected for p in points.values()], viewport)

    for constellation, raw_points in projected:
        report.constellations += 1
        placed = {star_id: fit(point) for star_id, point in raw_points.items()}

        segments = []
        for start, end in constellation.lines:
            if start in placed and end in placed:
                segments.append((placed[start], placed[end]))
            else:
                report.dropped_segments += 1
        report.segments += len(segments)

        if segments:
            parts.append('<g stroke="#5c6f9c" stroke-width="1.1" opacity="0.75">')
            for (x1, y1), (x2, y2) in segments:
                parts.append(f'<line x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}"/>')
            parts.append("</g>")

        for star in constellation.stars:
            x, y = placed[star.id]
            r = radius(star.mag)
            parts.append(
                f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" fill="{colour(star.spectral)}"/>'
            )
            report.stars += 1

        # Label at the centroid, which keeps it inside the figure without needing a
        # placement algorithm the agents have not written yet.
        if placed:
            cx = sum(p[0] for p in placed.values()) / len(placed)
            cy = sum(p[1] for p in placed.values()) / len(placed)
            parts.append(
                f'<text x="{_f(cx)}" y="{_f(cy - 18)}" fill="#8fa3d0" font-size="15" '
                f'font-family="Georgia, serif" text-anchor="middle">'
                f"{_escape(constellation.name)}</text>"
            )

    parts.append(legend(WIDTH, HEIGHT, constellations))

    if caption:
        parts.append(
            f'<text x="24" y="{HEIGHT - 22}" fill="#6b7ba8" font-size="16" '
            f'font-family="monospace">{_escape(caption)}</text>'
        )

    # Never silent about what was ignored.
    note = (
        f"constellations={report.constellations} stars={report.stars} "
        f"segments={report.segments} dropped_segments={report.dropped_segments} "
        f"skipped={','.join(report.skipped_files) or 'none'} "
        f"modules={report.modules}"
    )
    parts.append(f"<!-- {_escape(note)} -->")
    parts.append("</svg>")
    return "\n".join(parts), report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="workspace holding data/ and starmap/")
    parser.add_argument("--out", default="starmap.svg")
    parser.add_argument("--caption", default="", help="drawn bottom-left, e.g. a commit subject")
    parser.add_argument(
        "--viewbox",
        default="",
        help=(
            "pin the framing to 'min_x,min_y,max_x,max_y' in projected units instead of "
            "measuring the current points. The time-lapse uses this so the camera holds "
            "still while the sky fills up"
        ),
    )
    parser.add_argument(
        "--print-bounds",
        action="store_true",
        help="print the projected bounds of this state and exit; feeds --viewbox",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()

    if args.print_bounds:
        report = Report()
        found = load_constellations(root / "data" / "constellations", report)
        project = _load_projection(root, report)
        points = [project(star.ra, star.dec, WIDTH, HEIGHT) for c in found for star in c.stars]
        box = bounds(points)
        print("" if box is None else ",".join(f"{value:.6f}" for value in box))
        return 0

    viewport = None
    if args.viewbox.strip():
        try:
            values = [float(part) for part in args.viewbox.split(",")]
            if len(values) == 4:
                viewport = (values[0], values[1], values[2], values[3])
        except ValueError:
            raise SystemExit(f"--viewbox must be four numbers, got {args.viewbox!r}") from None

    svg, report = render_svg(root, args.caption, viewport)
    Path(args.out).write_text(svg, encoding="utf-8")

    if not args.quiet:
        print(
            f"{args.out}: {report.constellations} constellation(s), {report.stars} star(s), "
            f"{report.segments} segment(s)"
        )
        if report.skipped_files:
            print(f"  skipped: {', '.join(report.skipped_files)}")
        if report.dropped_segments:
            print(f"  dropped segments (unknown star id): {report.dropped_segments}")
        print(f"  modules: {report.modules}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
