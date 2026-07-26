"""The star map renderer must never fail, and must render the same bytes twice.

Both properties are load-bearing rather than nice to have. The artefact's whole point
is that any commit can be re-rendered into a time-lapse frame; a renderer that raises
on a half-finished commit would put the holes exactly where the interesting moments
are, and a renderer that is not deterministic would make the time-lapse flicker for
reasons that have nothing to do with the agents' work.

The inputs below are not invented worst cases. They are the shapes three unattended
agents at low effort actually produce: truncated JSON, a line pointing at a star that
was never defined, a module that imports but throws.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

RENDER_PY = Path(__file__).resolve().parents[1] / "demo" / "multivendor" / "starmap" / "render.py"


def _load_renderer():
    name = "_starmap_render_under_test"
    spec = importlib.util.spec_from_file_location(name, RENDER_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because @dataclass resolves annotations through
    # sys.modules; without this the module's own dataclasses fail to build.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


render = _load_renderer()


def _write(root: Path, slug: str, payload: object) -> None:
    directory = root / "data" / "constellations"
    directory.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (directory / f"{slug}.json").write_text(text, encoding="utf-8")


VALID = {
    "name": "The Lantern",
    "abbr": "Lan",
    "stars": [
        {"id": "a", "ra": 30.0, "dec": 20.0, "mag": 1.2, "spectral": "B"},
        {"id": "b", "ra": 45.0, "dec": 25.0, "mag": 2.4, "spectral": "M"},
    ],
    "lines": [["a", "b"]],
}


def test_renders_an_empty_workspace(tmp_path: Path):
    """The first commit of the run has no data at all and still needs a frame."""
    svg, report = render.render_svg(tmp_path)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert report.constellations == 0


def test_renders_one_constellation(tmp_path: Path):
    _write(tmp_path, "lantern", VALID)
    svg, report = render.render_svg(tmp_path)
    assert report.constellations == 1
    assert report.stars == 2
    assert report.segments == 1
    assert "The Lantern" in svg


@pytest.mark.parametrize(
    ("slug", "payload"),
    [
        ("truncated", "{ this is not json"),
        ("a_list", [1, 2, 3]),
        ("no_name", {"stars": [{"id": "a", "ra": 1.0, "dec": 1.0}]}),
        ("no_stars", {"name": "Empty", "stars": []}),
        ("ra_out_of_range", {"name": "Wrong", "stars": [{"id": "a", "ra": 999, "dec": 5}]}),
        ("dec_out_of_range", {"name": "Wrong", "stars": [{"id": "a", "ra": 5, "dec": -200}]}),
        ("star_not_an_object", {"name": "Odd", "stars": ["a star"]}),
    ],
)
def test_a_bad_file_is_skipped_and_named(tmp_path: Path, slug: str, payload: object):
    """One unusable file must cost its own constellation, not the whole image."""
    _write(tmp_path, "lantern", VALID)
    _write(tmp_path, slug, payload)

    svg, report = render.render_svg(tmp_path)

    assert report.constellations == 1, "the good constellation survived"
    assert f"{slug}.json" in report.skipped_files
    assert svg.rstrip().endswith("</svg>")


def test_a_line_to_an_undefined_star_is_dropped_not_fatal(tmp_path: Path):
    payload = dict(VALID, lines=[["a", "b"], ["b", "never-defined"]])
    _write(tmp_path, "lantern", payload)

    _, report = render.render_svg(tmp_path)

    assert report.segments == 1
    assert report.dropped_segments == 1


def _write_module(root: Path, name: str, source: str) -> None:
    directory = root / "starmap"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(source, encoding="utf-8")


def test_an_optional_module_that_throws_falls_back(tmp_path: Path):
    _write(tmp_path, "lantern", VALID)
    _write_module(
        tmp_path, "projection", 'def project(a, b, c, d):\n    raise RuntimeError("boom")\n'
    )

    svg, report = render.render_svg(tmp_path)

    assert report.stars == 2, "stars were still placed, by the fallback projection"
    assert svg.rstrip().endswith("</svg>")


def test_an_optional_module_returning_nonsense_falls_back(tmp_path: Path):
    """A wrong-looking colour or a negative radius must not reach the SVG."""
    _write(tmp_path, "lantern", VALID)
    _write_module(
        tmp_path,
        "style",
        'def star_colour(s):\n    return "not a colour"\n\n\n'
        "def star_radius(m):\n    return -999\n",
    )

    svg, _ = render.render_svg(tmp_path)

    assert "not a colour" not in svg
    assert 'r="-' not in svg


def test_a_module_that_does_not_import_is_reported(tmp_path: Path):
    _write(tmp_path, "lantern", VALID)
    _write_module(tmp_path, "legend", "this is not python(((")

    _, report = render.render_svg(tmp_path)

    assert any("legend" in entry for entry in report.skipped_files)


def test_two_renders_are_byte_identical(tmp_path: Path):
    """Without this the time-lapse would flicker for reasons unrelated to the agents."""
    _write(tmp_path, "lantern", VALID)
    _write(tmp_path, "second", dict(VALID, name="The Kite", abbr="Kit"))

    first, _ = render.render_svg(tmp_path)
    second, _ = render.render_svg(tmp_path)

    assert first == second


def test_the_svg_records_what_it_ignored(tmp_path: Path):
    """A silently thinner picture is worse than a noted one."""
    _write(tmp_path, "lantern", VALID)
    _write(tmp_path, "truncated", "{ nope")

    svg, _ = render.render_svg(tmp_path)

    assert "truncated.json" in svg
