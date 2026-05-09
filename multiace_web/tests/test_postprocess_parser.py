"""Tests for multiace_postprocess.parse_header()."""
import sys
from pathlib import Path

import pytest

# Import directly from tools/ (not installed package)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import multiace_postprocess as pp


FIXTURE = Path(__file__).parent / "fixtures" / "sample_8color.gcode"


def test_parse_header_extracts_all_8_tools():
    lines = FIXTURE.read_text().splitlines()
    tools = pp.parse_header(lines)
    assert len(tools) == 8


def test_parse_header_normalizes_type_uppercase():
    lines = FIXTURE.read_text().splitlines()
    tools = pp.parse_header(lines)
    assert tools[0]["type"] == "PLA"
    assert tools[2]["type"] == "PETG"
    assert tools[3]["type"] == "TPU"


def test_parse_header_normalizes_color_lowercase_no_hash():
    lines = FIXTURE.read_text().splitlines()
    tools = pp.parse_header(lines)
    assert tools[0]["color"] == "ff0000"
    assert tools[1]["color"] == "ffffff"
    assert tools[2]["color"] == "0080ff"
    assert tools[7]["color"] == "80ff00"


def test_parse_header_returns_none_when_no_filament_type_comment():
    lines = ["; no filament metadata here", "G28", "T0"]
    result = pp.parse_header(lines)
    assert result is None


def test_parse_header_returns_none_when_fewer_than_2_tools():
    lines = [
        "; filament_type = PLA",
        "; filament_colour = #FF0000",
        "G28",
    ]
    result = pp.parse_header(lines)
    assert result is None
