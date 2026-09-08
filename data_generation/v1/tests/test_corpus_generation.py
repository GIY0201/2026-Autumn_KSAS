"""Catalog coverage and UI counts must use real object configuration."""

import pytest
from omegaconf import OmegaConf

from data_generation.v1 import generate
from data_generation.v1.service import list_presets


@pytest.mark.parametrize("object_id,count", [("fixed_wing", 64), ("helicopter", 96), ("vtol", 64)])
def test_corpus_schedule_covers_each_scenario_and_repeats(object_id, count):
    assert hasattr(generate, "corpus_schedule"), "catalog batch generation is missing"
    config = OmegaConf.create(
        {
            "profile_id": f"{object_id}_full_flight_v1",
            "mode": "corpus",
            "repeats_per_scenario": 2,
            "seed": 17,
        }
    )
    schedule = generate.corpus_schedule(config)
    assert len(schedule) == count * 2
    assert len({scenario for scenario, seed in schedule}) == count
    assert len({seed for scenario, seed in schedule}) == count * 2
    assert schedule == generate.corpus_schedule(config)


def test_ui_exposes_catalog_generation_with_training_preparation():
    presets = {item.preset_id: item for item in list_presets()}
    assert {"corpus_fixed_wing", "corpus_helicopter", "corpus_vtol"} <= set(presets)
    assert presets["corpus_helicopter"].episode_count == 96
