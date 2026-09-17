#!/usr/bin/env python3
"""
Command line entry point for the pipeline orchestrator.

Usage:
    python -m orchestrator                      # run every stage
    python -m orchestrator --dry-run            # print resolved commands, run nothing
    python -m orchestrator --only plan          # run one stage
    python -m orchestrator --from embed         # resume from a stage

Author: Andrew LaPointe
Date: 2026-09-17
"""

import logging
import sys
from argparse import ArgumentParser
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, ConfigError, PipelineConfig, Stage
from .config import load_pipeline_config
from .runner import StageError, run_pipeline

logger = logging.getLogger("orchestrator")


def select_stages(
    config: PipelineConfig,
    only: list[str] | None,
    from_stage: str | None,
    to_stage: str | None,
) -> list[Stage]:
    """Narrow the pipeline to the stages the user asked for, keeping config order."""
    if only:
        wanted = [config.stage(name).name for name in only]
        return [stage for stage in config.stages if stage.name in wanted]

    names = [stage.name for stage in config.stages]
    start = names.index(config.stage(from_stage).name) if from_stage else 0
    end = names.index(config.stage(to_stage).name) + 1 if to_stage else len(names)

    if start >= end:
        raise ConfigError(
            f"--from {from_stage} comes after --to {to_stage} in the pipeline."
        )

    return config.stages[start:end]


def apply_overrides(config: PipelineConfig, overrides: list[str]) -> None:
    """Apply KEY=VALUE overrides to the config's artifacts and settings.

    A key already naming an artifact overrides that path; anything else overrides
    or adds a setting.
    """
    for override in overrides:
        key, separator, value = override.partition("=")
        if not separator or not key:
            raise ConfigError(f"Malformed --set '{override}'. Expected KEY=VALUE.")

        if key in config.artifacts:
            config.artifacts[key] = value
        else:
            config.settings[key] = value


def setup_logging(config: PipelineConfig, level: str | None) -> None:
    """Log to the console and, when the config names one, to a file."""
    settings = config.logging
    resolved = getattr(logging, level or str(settings.get("level", "INFO")))
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]

    log_file = settings.get("file")
    if log_file:
        path = config.workdir / str(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path))

    logging.basicConfig(
        level=resolved,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


def main() -> None:
    parser = ArgumentParser(description=__doc__.split("Usage:")[0].strip())
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the pipeline configuration file.",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="STAGE",
        help="Run only these stages, in pipeline order.",
    )
    parser.add_argument(
        "--from",
        dest="from_stage",
        metavar="STAGE",
        help="Start from this stage instead of the first.",
    )
    parser.add_argument(
        "--to",
        dest="to_stage",
        metavar="STAGE",
        help="Stop after this stage instead of the last.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print the resolved command for each stage without running anything.",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override one setting or artifact path for this run. Repeatable.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override the log level from the config.",
    )
    args = parser.parse_args()

    if args.only and (args.from_stage or args.to_stage):
        parser.error("--only cannot be combined with --from/--to.")

    try:
        config = load_pipeline_config(args.config)
        apply_overrides(config, args.overrides)
        stages = select_stages(config, args.only, args.from_stage, args.to_stage)
    except ConfigError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)

    setup_logging(config, args.log_level)

    try:
        run_pipeline(config, stages, dry_run=args.dry_run)
    except StageError as error:
        logger.error("%s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
