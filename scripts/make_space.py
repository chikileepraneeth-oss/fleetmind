"""
Prepare the public demo for Hugging Face Spaces (a free host for Docker apps).

    python -m scripts.make_space                       # writes dist/hf-space/
    python -m scripts.make_space --github-url https://github.com/<you>/fleetmind

dist/hf-space/ then holds everything the Space needs: the Dockerfile, the code, the
layout, the 3D world and a README with the Space settings at the top (Hugging Face reads
them from there). Push that folder to your Space; see "Put the public demo online" in README.md.

Nothing secret goes in: no .env, no tests, no git history, no virtual environment.
"""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "hf-space"
COPY = ["Dockerfile", ".dockerignore", "requirements.txt", "layouts", "scripts", "web", "viewer", "isaac",
        "data/orders_hourly.csv"]
SKIP = {"__pycache__", "test_recordings.js"}

SPACE_README = """---
title: FleetMind
emoji: 🤖
colorFrom: blue
colorTo: green
sdk: docker
app_port: 8000
pinned: false
short_description: A warehouse robot fleet with a central brain, live in 2D and 3D
---

# FleetMind: live demo

A warehouse where 5 robots deliver orders, with one central brain that decides which robot
does what, which way it drives and when it charges. The robots talk VDA 5050 messages, like
real warehouse robots. Press **Start**, pick a scenario, or start a **random day**. The 3D view
is at `/3d`.

You share this warehouse with everyone watching. This demo has no AI and no tools switched on:
it only shows the work.

Code, tests and results: {github}
"""


def ignore(directory, names):
    return [n for n in names if n in SKIP or n.endswith(".pyc")]


def main():
    parser = argparse.ArgumentParser(description="Prepare dist/hf-space/ for a Hugging Face Space.")
    parser.add_argument("--github-url", default="https://github.com/chikileepraneeth-oss/fleetmind")
    args = parser.parse_args()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for item in COPY:
        source = ROOT / item
        target = OUT / item
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=ignore)
        else:
            shutil.copy2(source, target)
    (OUT / "README.md").write_text(SPACE_README.format(github=args.github_url), encoding="utf-8")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    biggest = max((f for f in OUT.rglob("*") if f.is_file()), key=lambda f: f.stat().st_size)
    print(f"Wrote {OUT} ({size:.1f} MB; biggest file {biggest.relative_to(OUT)}, "
          f"{biggest.stat().st_size / 1e6:.1f} MB)")
    print("Next: push this folder to your Space (see 'Put the public demo online' in README.md).")


if __name__ == "__main__":
    main()
