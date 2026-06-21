"""Tests for tools/generate_testmatrix.py — builds a 12-color multiACE
calibration plate as a 3MF (zip of XML)."""
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import generate_testmatrix as g


def test_write_3mf_produces_valid_container(tmp_path):
    out = tmp_path / "testmatrix.3mf"
    g.write_3mf(str(out))
    assert out.exists()
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
    assert "[Content_Types].xml" in names
    assert "_rels/.rels" in names
    assert "3D/3dmodel.model" in names
    assert "Metadata/model_settings.config" in names


def test_model_has_expected_object_and_item_counts(tmp_path):
    out = tmp_path / "tm.3mf"
    g.write_3mf(str(out))
    with zipfile.ZipFile(out) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8")
    # 12 cells -> 24 leaf objects (body+glyph) + 12 assembly objects = 36.
    assert model.count("<object ") == 36
    # one build item per cell.
    assert model.count("<item ") == g.N_CELLS == 12


def test_each_cell_references_its_two_extruders(tmp_path):
    out = tmp_path / "tm.3mf"
    g.write_3mf(str(out))
    with zipfile.ZipFile(out) as z:
        cfg = z.read("Metadata/model_settings.config").decode("utf-8")
    # body_T0..body_T11 and glyph_T0..glyph_T11 all present.
    for i in range(12):
        assert ('value="body_T%d"' % i) in cfg
        assert ('value="glyph_T%d"' % i) in cfg


def test_glyph_and_body_geometry_helpers():
    # The digit glyph for cell 0 has "on" pixels, and the layer-2 body tiles
    # the footprint around them (both non-empty).
    assert len(g.glyph_rects(0)) > 0
    assert len(g.body_layer2_rects(0)) > 0


def test_main_writes_to_out_path(tmp_path):
    out = tmp_path / "viamain.3mf"
    rc = g.main(["--out", str(out)])
    assert rc == 0
    assert zipfile.is_zipfile(str(out))
