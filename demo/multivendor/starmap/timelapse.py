"""Rebuild the sky at every commit, so the time-lapse is evidence rather than an edit.

The artefact is data plus a fixed renderer, which means any past state of it can be
reproduced exactly. Walking the git history and re-rendering each commit therefore
produces a sequence that *is* the run, frame by frame, with real timestamps attached --
as opposed to a screen recording, which shows only that something was recorded.

Two properties make it usable as evidence:

  - Frames come from commits, not from a capture. If a frame looks wrong, the commit it
    came from can be checked out and inspected.
  - The renderer is deterministic, so re-running this produces the same frames. Nobody
    has to trust the machine it first ran on.

The work happens in a throwaway clone. The live workspace is never checked out from
under an agent that might still be writing to it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER_PY = HERE / "render.py"


@dataclass
class Frame:
    index: int
    sha: str
    committed_at: str
    subject: str
    author: str
    file: str
    constellations: int = 0
    stars: int = 0
    segments: int = 0
    skipped: list[str] = field(default_factory=list)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def commits(repo: Path) -> list[tuple[str, str, str, str]]:
    """Oldest first: (sha, iso timestamp, author, subject)."""
    raw = git(repo, "log", "--reverse", "--format=%H%x1f%ct%x1f%an%x1f%s")
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, epoch, author, subject = line.split("\x1f", 3)
        stamp = datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
        out.append((sha, stamp, author, subject))
    return out


def final_viewbox(tree: Path) -> str:
    """The framing of the finished sky, reused for every earlier frame.

    Without this the renderer would fit each frame to whatever existed at the time, so
    the camera would zoom out on every new constellation and nothing would stay where
    the eye left it. Pinning to the final state instead lets the sky fill a still frame.
    """
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(RENDER_PY), "--root", str(tree), "--print-bounds"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def render_frame(tree: Path, out_file: Path, caption: str, viewbox: str = "") -> dict:
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(RENDER_PY),
            "--root",
            str(tree),
            "--out",
            str(out_file),
            "--caption",
            caption,
            *(["--viewbox", viewbox] if viewbox else []),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        # The renderer is built not to fail; if it ever does, say so loudly rather than
        # leaving a gap in the sequence that looks like nothing happened.
        raise SystemExit(
            f"renderer failed on {out_file.name} (exit {result.returncode}):\n{result.stderr}"
        )
    return {"stdout": result.stdout.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="the run workspace (a git repo)")
    parser.add_argument("--out", required=True, help="directory to write frames into")
    parser.add_argument(
        "--keep-clone", action="store_true", help="leave the throwaway clone in place"
    )
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    if not (workspace / ".git").exists():
        raise SystemExit(f"{workspace} is not a git repository, so it has no timeline")

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    scratch = Path(tempfile.mkdtemp(prefix="starmap-timelapse-"))
    clone = scratch / "tree"
    try:
        git(scratch, "clone", "--quiet", str(workspace), str(clone))
        history = commits(clone)
        if not history:
            raise SystemExit("no commits, so no time-lapse")

        # Measured at HEAD, before walking back, so every frame shares one camera.
        viewbox = final_viewbox(clone)

        print(f"{len(history)} commit(s) -> {out_dir}")
        if viewbox:
            print(f"framing pinned to the final state: {viewbox}")
        frames: list[Frame] = []

        for index, (sha, stamp, author, subject) in enumerate(history, start=1):
            git(clone, "checkout", "--quiet", "--detach", sha)
            name = f"frame-{index:04d}.svg"
            caption = f"{index:02d}/{len(history)}  {stamp[11:19]}Z  {subject[:70]}"
            info = render_frame(clone, out_dir / name, caption, viewbox)

            frame = Frame(
                index=index,
                sha=sha,
                committed_at=stamp,
                subject=subject,
                author=author,
                file=name,
            )
            # The renderer prints its own counts; parsed back so the manifest can be
            # read without opening every SVG.
            for token, attribute in (
                ("constellation(s)", "constellations"),
                ("star(s)", "stars"),
                ("segment(s)", "segments"),
            ):
                words = info["stdout"].replace(",", " ").split()
                if token in words:
                    position = words.index(token)
                    if position > 0 and words[position - 1].isdigit():
                        setattr(frame, attribute, int(words[position - 1]))
            frames.append(frame)
            print(f"  {name}  {stamp[11:19]}Z  {subject[:60]}")

        manifest = out_dir / "frames.json"
        manifest.write_text(
            json.dumps(
                {
                    "frames": [frame.__dict__ for frame in frames],
                    "first_commit": history[0][1],
                    "last_commit": history[-1][1],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nmanifest: {manifest}")
        return 0
    finally:
        if args.keep_clone:
            print(f"clone kept at {clone}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
