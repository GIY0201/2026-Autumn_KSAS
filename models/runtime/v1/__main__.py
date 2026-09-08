"""Explicit local training command using versioned OmegaConf settings."""

import argparse
from pathlib import Path

from omegaconf import OmegaConf

from .service import TrainingService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--parent-checkpoint")
    parser.add_argument("overrides", nargs="*")
    args = parser.parse_args()
    config = OmegaConf.to_container(
        OmegaConf.merge(OmegaConf.load(args.config), OmegaConf.from_dotlist(args.overrides)),
        resolve=True,
    )
    service = TrainingService(Path(__file__).resolve().parents[3])
    print(service.start(config, args.name, args.parent_checkpoint), flush=True)


if __name__ == "__main__":
    main()
