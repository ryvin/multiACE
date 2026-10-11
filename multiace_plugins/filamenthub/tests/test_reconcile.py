# License: GPL-3.0
"""Unit tests for the pure reconcile planner."""
from filamenthub_plugin.reconcile import plan_reconcile, reconcile_slots


def _row(ace, slot, spool_id, material="PLA", color="#ffffff", name="n"):
    return {"ace": ace, "slot": slot, "spool_id": spool_id,
            "material": material, "color": color, "name": name}


def test_apply_maps_winners_with_brand():
    winners = [_row(0, 0, 42)]
    to_apply, to_clear = plan_reconcile(winners, [], {42: "PolyTerra"})
    assert to_apply == [{"ace": 0, "slot": 0, "material": "PLA",
                         "brand": "PolyTerra", "subtype": "n", "color": "#ffffff"}]
    assert to_clear == []


def test_clears_vacated_slot_on_known_ace():
    # ACE 0 has a winner at slot 0; slot 1 is currently labeled but no longer a
    # winner -> cleared (same ACE = FilamentHub-known).
    winners = [_row(0, 0, 42)]
    to_apply, to_clear = plan_reconcile(winners, ["0_0", "0_1"], {})
    assert to_clear == [(0, 1)]


def test_does_not_clear_unknown_ace():
    # ACE 1 is not in the winner set -> its labels are left untouched.
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(winners, ["0_0", "1_3"], {})
    assert to_clear == []


def test_does_not_clear_a_slot_that_is_still_a_winner():
    winners = [_row(0, 0, 42), _row(0, 1, 43)]
    _, to_clear = plan_reconcile(winners, ["0_0", "0_1"], {})
    assert to_clear == []


def test_does_not_clear_a_disputed_slot():
    # ACE0 slot0 is a winner (keeps ACE0 "known"). Slot1 has a current override
    # but is momentarily absent from the winner set — however it is DISPUTED, so
    # a transient winner drop must NOT delete the contested label. Disputes are
    # shown, never written; deleting an override is a write.
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(
        winners, ["0_0", "0_1"], {}, disputed_keys={(0, 1)})
    assert to_clear == []


def test_still_clears_vacated_non_disputed_slot_when_disputes_present():
    # A genuinely vacated, non-disputed slot is still cleared even if some other
    # slot is disputed.
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(
        winners, ["0_0", "0_1", "0_2"], {}, disputed_keys={(0, 2)})
    assert to_clear == [(0, 1)]


def test_skips_winner_with_none_slot():
    winners = [{"ace": 0, "slot": None, "spool_id": 1,
                "material": "PLA", "color": "#fff", "name": "n"}]
    to_apply, to_clear = plan_reconcile(winners, [], {})
    assert to_apply == []


def test_ignores_malformed_override_keys():
    winners = [_row(0, 0, 42)]
    _, to_clear = plan_reconcile(winners, ["0_0", "bogus", "0_x"], {})
    assert to_clear == []


def _obs(idx, slots):
    return [{"idx": idx, "slots": slots}]

def _slot(i, state, material="", color=None, rfid=0):
    return {"idx": i, "state": state, "material": material, "color": color, "rfid": rfid}

_RED = {"ace": 0, "slot": 2, "spool_id": 110, "material": "PLA",
        "brand": "Snapmaker", "subtype": "SnapSpeed Red", "color": "#FF0000"}
_WHITE = {"ace": 0, "slot": 3, "spool_id": 91, "material": "PLA",
          "brand": "Snapmaker", "subtype": "SnapSpeed Pearl White", "color": "#F8F8FF"}

def test_expected_not_loaded_when_desired_but_empty():
    rows = reconcile_slots({"0_2": _RED}, _obs(0, [_slot(2, "empty")]))
    r = next(x for x in rows if (x["ace"], x["slot"]) == (0, 2))
    assert r["recon_state"] == "EXPECTED_NOT_LOADED"
    assert r["display_name"] == "SnapSpeed Red"

def test_unknown_loaded_when_occupied_no_desired():
    rows = reconcile_slots({}, _obs(0, [_slot(0, "ready", rfid=1)]))
    r = rows[0]
    assert r["recon_state"] == "UNKNOWN_LOADED"

def test_verified_when_rfid_matches_desired():
    rows = reconcile_slots({"0_3": _WHITE},
                           _obs(0, [_slot(3, "ready", material="PLA", color="#F8F8FF", rfid=1)]))
    assert rows[0]["recon_state"] == "VERIFIED"

def test_asserted_when_occupied_desired_no_rfid_identity():
    rows = reconcile_slots({"0_3": _WHITE},
                           _obs(0, [_slot(3, "ready", material="", color=None, rfid=1)]))
    assert rows[0]["recon_state"] == "ASSERTED"

def test_conflict_when_rfid_disagrees():
    rows = reconcile_slots({"0_3": _WHITE},
                           _obs(0, [_slot(3, "ready", material="PETG", color="#000000", rfid=1)]))
    assert rows[0]["recon_state"] == "CONFLICT"

def test_empty_when_neither():
    rows = reconcile_slots({}, _obs(0, [_slot(1, "empty")]))
    assert rows[0]["recon_state"] == "EMPTY"
