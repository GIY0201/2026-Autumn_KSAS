"""Catalog coverage and UI counts must use real object configuration."""

import pytest
from omegaconf import OmegaConf

from data_generation.v1 import generate
from data_generation.v1.full_flight_config import catalog
from data_generation.v1.profiles import load_generation_profile
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
    assert len({entry.scenario_id for entry in schedule}) == count
    assert len({entry.seed for entry in schedule}) == count * 2
    assert schedule == generate.corpus_schedule(config)


def test_vtol_balanced_schedule_equalizes_split_actions_and_directions():
    config = OmegaConf.load("data_generation/v1/configs/corpus_vtol.yaml")
    schedule = generate.corpus_schedule(config)
    assert len(schedule) == 160
    assert {split: sum(entry.split == split for entry in schedule) for split in (
        "train", "validation", "test"
    )} == {"train": 112, "validation": 24, "test": 24}

    profile = load_generation_profile("vtol_full_flight_v1")
    choices = catalog(profile["motion"], "vtol")
    turning = {"turn", "s_turn", "spiral", "orbit"}
    for split in ("train", "validation", "test"):
        selected = [entry for entry in schedule if entry.split == split]
        for phase in ("climb", "cruise", "descent"):
            counts = {
                action: sum(choices[entry.scenario_id][phase] == action for entry in selected)
                for action in profile["motion"]["objects"]["vtol"][phase]
            }
            assert len(set(counts.values())) == 1
            for action in turning & set(counts):
                directions = {
                    direction: sum(
                        choices[entry.scenario_id][phase] == action
                        and entry.direction == direction
                        for entry in selected
                    )
                    for direction in (-1, 1)
                }
                assert len(set(directions.values())) == 1
    assert schedule == generate.corpus_schedule(config)


def test_ui_exposes_catalog_generation_with_training_preparation():
    presets = {item.preset_id: item for item in list_presets()}
    assert {"corpus_fixed_wing", "corpus_helicopter", "corpus_vtol"} <= set(presets)
    assert presets["corpus_helicopter"].episode_count == 96
    assert presets["corpus_vtol"].episode_count == 160
