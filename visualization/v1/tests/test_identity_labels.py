"""Object names must come from public metadata, never an inferred aircraft type."""

from pathlib import Path

import pytest

from contracts.v1.types import EpisodeMetadata, PublicDataset
from visualization.v1.figures import ViewerDataset


@pytest.mark.parametrize("episode_id", ["x8-legacy", "quadrotor-17", "unclassified-42"])
def test_unknown_identity_stays_unknown_even_when_an_id_looks_like_a_known_type(
    episode_id: str,
) -> None:
    dataset = ViewerDataset(
        source_path=Path("unused"),
        public=PublicDataset(
            episodes=(EpisodeMetadata(episode_id, "diagnostic", 60.0, 301, 0.2, "Unknown"),),
            observations=(),
        ),
        _observations={},
        _truth={},
    )

    assert dataset.episode_display_name(episode_id) == "Unknown · 진단 시뮬레이션 1"


def test_object_identity_is_not_replaced_by_the_viewers_original_aircraft() -> None:
    dataset = ViewerDataset(
        source_path=Path("unused"),
        public=PublicDataset(
            episodes=(
                EpisodeMetadata("sample-17", "diagnostic", 60.0, 301, 0.2, "쿼드콥터 · Crazyflie"),
            ),
            observations=(),
        ),
        _observations={},
        _truth={},
    )

    assert dataset.episode_option_label("sample-17") == (
        "쿼드콥터 · Crazyflie · 진단 시뮬레이션 1 (ID: sample-17)"
    )
