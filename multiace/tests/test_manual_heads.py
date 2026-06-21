"""Tests for manual_heads.py — the Klipper-free decision/serialization layer
for the Manual Heads / TPU feature. A head marked "manual" is hand-fed
(flexible filament) and bypasses ACE feed/feed-assist/retract/park."""
import manual_heads as mh


# --- parse_manual_heads ----------------------------------------------------

def test_parse_empty_is_empty():
    assert mh.parse_manual_heads("") == frozenset()
    assert mh.parse_manual_heads(None) == frozenset()


def test_parse_single_and_multi():
    assert mh.parse_manual_heads("0") == frozenset({0})
    assert mh.parse_manual_heads("0,3") == frozenset({0, 3})


def test_parse_tolerates_whitespace_and_dupes():
    assert mh.parse_manual_heads(" 0 , 3 , 0 ") == frozenset({0, 3})


def test_parse_drops_out_of_range_and_garbage():
    assert mh.parse_manual_heads("4") == frozenset()        # head_count default 4 -> valid 0..3
    assert mh.parse_manual_heads("-1") == frozenset()
    assert mh.parse_manual_heads("x") == frozenset()
    assert mh.parse_manual_heads("0,x,2") == frozenset({0, 2})


# --- head_manual_bypasses --------------------------------------------------

def test_bypass_true_when_in_set():
    s = frozenset({0, 3})
    assert mh.head_manual_bypasses(0, s) is True
    assert mh.head_manual_bypasses(3, s) is True


def test_bypass_false_when_not_in_set():
    assert mh.head_manual_bypasses(1, frozenset({0, 3})) is False
    assert mh.head_manual_bypasses(2, frozenset()) is False


def test_bypass_accepts_str_head_and_rejects_garbage():
    s = frozenset({0})
    assert mh.head_manual_bypasses("0", s) is True
    assert mh.head_manual_bypasses(None, s) is False
    assert mh.head_manual_bypasses("nope", s) is False


# --- serialize / deserialize (save_variables round-trip) -------------------

def test_serialize_is_sorted_json_list():
    assert mh.serialize_manual_heads({3, 0}) == "[0, 3]"
    assert mh.serialize_manual_heads(set()) == "[]"


def test_deserialize_from_json_string():
    assert mh.deserialize_manual_heads("[0, 3]") == {0, 3}


def test_deserialize_from_list_value():
    # save_variables may hand back an already-parsed list
    assert mh.deserialize_manual_heads([0, 3]) == {0, 3}


def test_deserialize_drops_out_of_range_and_bad_values():
    assert mh.deserialize_manual_heads("[0, 4, -1]") == {0}
    assert mh.deserialize_manual_heads("not json") == set()
    assert mh.deserialize_manual_heads(None) == set()
    assert mh.deserialize_manual_heads({"0": True}) == set()  # wrong shape


def test_roundtrip():
    s = {0, 3}
    assert mh.deserialize_manual_heads(mh.serialize_manual_heads(s)) == s
