"""The runtime campaign is a compilation of the step graph, not a fixed script."""

from dynamical.backends._runtime_pack import runtime_campaign, runtime_capability_bindings


def test_campaign_follows_the_composition_order(three_station_composition):
    # Actions are kinded by the facility-declared action_type bound to each
    # operation, not the raw operation_id: manifests/ac-electrodeposition-cell.yaml
    # declares action_type "dispense" for operation_id "dispense-electrolyte"
    # (and "electrodeposit" / "measure" for the other two) -- they are not equal.
    campaign = runtime_campaign(three_station_composition)
    kinds = [action["kind"] for action in campaign["actions"]]
    assert kinds.index("dispense") < kinds.index("electrodeposit")
    assert kinds.index("electrodeposit") < kinds.index("measure")


def test_two_instruments_may_share_an_action_type(two_observer_composition):
    bindings = runtime_capability_bindings(two_observer_composition)
    keys = {(b["instrument_id"], b["action_type"]) for b in bindings}
    assert len(keys) == 2, "keying by action_type alone collides across instruments"


def test_no_action_appears_that_the_composition_did_not_select(three_station_composition):
    campaign = runtime_campaign(three_station_composition)
    bindings = runtime_capability_bindings(
        three_station_composition,
        operation_bindings=three_station_composition.operation_bindings,
    )
    selected = {b["action_type"] for b in bindings}
    emitted = {a["kind"] for a in campaign["actions"] if a["kind"] not in {"wait", "observe"}}
    assert emitted <= selected, f"unselected actions were injected: {emitted - selected}"
