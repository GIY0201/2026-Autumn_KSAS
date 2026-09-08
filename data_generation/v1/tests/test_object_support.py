"""Keep requested objects visible without advertising unimplemented generators."""

from data_generation.v1 import service


def test_object_readiness_is_derived_from_actual_presets() -> None:
    presets = service.list_presets()
    support = service.list_object_support(presets)
    assert {item.object_id for item in support} == {
        "fixed_wing",
        "x8_fixed_wing",
        "quadrotor",
        "vtol",
        "helicopter",
    }
    for item in support:
        matches = tuple(p.preset_id for p in presets if p.object_id == item.object_id)
        assert item.preset_ids == matches
        assert item.available == bool(matches)
        assert item.label
        if not matches:
            assert item.unavailable_reason
    by_id = {item.object_id: item for item in support}
    assert by_id["x8_fixed_wing"].available
    assert by_id["quadrotor"].available
    assert by_id["x8_fixed_wing"].preset_ids == (
        "diagnostic_fixed_wing_point_mass",
        "pilot_fixed_wing_point_mass",
    )
    assert by_id["quadrotor"].preset_ids == (
        "diagnostic_quadrotor_point_mass",
        "pilot_quadrotor_point_mass",
    )
    assert by_id["vtol"].available
    assert by_id["vtol"].preset_ids == (
        "corpus_vtol",
        "diagnostic_vtol",
        "full_flight_vtol",
        "pilot_vtol",
    )
    assert by_id["helicopter"].available
    assert by_id["helicopter"].preset_ids == (
        "corpus_helicopter",
        "diagnostic_helicopter",
        "full_flight_helicopter",
        "pilot_helicopter",
    )
