"""
Run all tests AND record them for the visual test viewer.

Usage (from the fleetmind folder):
    python -m scripts.record_tests

Then open viewer/tests.html in your browser.

(Plain `python -m pytest` still runs the tests normally, without recording.)
"""

import os
import sys

import pytest


def main():
    os.environ["FLEETMIND_RECORD_TESTS"] = "1"
    # -p no:cacheprovider keeps the run clean; -q keeps the output short
    sys.exit(pytest.main(["-q", "-p", "no:cacheprovider", "tests"]))


if __name__ == "__main__":
    main()