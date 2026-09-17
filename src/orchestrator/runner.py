#!/usr/bin/env python3
"""
Runs pipeline stages to completion, one after another, each in its own conda
environment via `conda run`.

Stages hand work to each other through files on disk, not through memory: the
retrieval stage needs faiss and the embedding stage needs torch, and loading both
in one process aborts the interpreter (see src/ontology_rag/README.md). Running
each stage as its own process in its own environment is what makes that safe.

Author: Andrew LaPointe
Date: 2026-09-17
"""

import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

from .config import PipelineConfig, Stage, conda_environments

logger = logging.getLogger(__name__)


class StageError(Exception):
    """Raised when a stage exits non-zero, times out, or produces no output."""


def build_argv(stage: Stage, config: PipelineConfig) -> list[str]:
    """Build the full `conda run` argument vector for a stage.

    Arguments:
        stage (Stage): The stage to run.
        config (PipelineConfig): The pipeline the stage belongs to.

    Returns:
        (list[str]): Argument vector suitable for subprocess.run(shell=False).
    """
    return [
        "conda",
        "run",
        "-n",
        stage.env,
        "--no-capture-output",
        "--cwd",
        str(config.workdir),
        *shlex.split(config.resolve(stage)),
    ]


def build_env(config: PipelineConfig) -> dict[str, str]:
    """Child environment for a stage: the current one plus PYTHONPATH=src.

    Both ontology_rag and ontology_selection import as packages from src/, so every
    stage needs src/ on the path (scripts/rag.sh does the same).
    """
    env = dict(os.environ)
    src = str(config.workdir / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return env


def run_stage(stage: Stage, config: PipelineConfig, dry_run: bool = False) -> float:
    """Run one stage to completion.

    Arguments:
        stage (Stage): The stage to run.
        config (PipelineConfig): The pipeline the stage belongs to.
        dry_run (bool): Print the resolved command and return without running it.

    Returns:
        (float): Elapsed wall-clock seconds, or 0.0 for a dry run.

    Raises:
        StageError: If the command exits non-zero, times out, or leaves a declared
            output missing.
    """
    argv = build_argv(stage, config)

    if dry_run:
        print(f"[{stage.name}] {shlex.join(argv)}")
        return 0.0

    for artifact in stage.outputs:
        config.path(artifact).parent.mkdir(parents=True, exist_ok=True)

    logger.info("stage=%s env=%s status=start", stage.name, stage.env)
    logger.debug("stage=%s command=%s", stage.name, shlex.join(argv))

    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            cwd=config.workdir,
            env=build_env(config),
            timeout=stage.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise StageError(
            f"Stage '{stage.name}' timed out after {stage.timeout}s."
        ) from error
    elapsed = time.monotonic() - started

    if result.returncode != 0:
        raise StageError(
            f"Stage '{stage.name}' exited {result.returncode} after {elapsed:.1f}s.\n"
            f"  command: {shlex.join(argv)}"
        )

    missing = [a for a in stage.outputs if not config.path(a).exists()]
    if missing:
        raise StageError(
            f"Stage '{stage.name}' exited 0 but did not write: "
            f"{', '.join(str(config.path(a)) for a in missing)}."
        )

    logger.info(
        "stage=%s env=%s status=ok elapsed=%.1fs", stage.name, stage.env, elapsed
    )
    return elapsed


def _check_passthrough(config: PipelineConfig) -> None:
    """Note any declared passthrough variable the shell does not set.

    Not an error: a stage may pick the value up elsewhere, as the LLM stages do
    from src/ontology_selection/.env.
    """
    unset = [name for name in config.env_passthrough if name not in os.environ]
    if unset:
        logger.info(
            "not set in this shell: %s (stages must find them elsewhere, "
            "e.g. src/ontology_selection/.env)",
            ", ".join(unset),
        )


def _check_environments(stages: list[Stage], dry_run: bool) -> None:
    """Fail before any stage starts if a selected stage's conda env is missing."""
    available = conda_environments()
    if available is None:
        return

    for stage in stages:
        if stage.env in available:
            continue
        message = (
            f"Stage '{stage.name}' needs conda environment '{stage.env}', which does "
            f"not exist. Available: {', '.join(sorted(available))}."
        )
        if dry_run:
            logger.warning("%s", message)
        else:
            raise StageError(message)


def run_pipeline(
    config: PipelineConfig, stages: list[Stage], dry_run: bool = False
) -> None:
    """Run the selected stages in order, stopping at the first failure.

    Inputs are checked up front unless an earlier selected stage produces them, so
    running a single stage against missing intermediates fails immediately with a
    message naming the file rather than inside the stage's own code.
    """
    _check_environments(stages, dry_run)
    _check_passthrough(config)

    produced: set[str] = set()
    for stage in stages:
        missing = [
            a
            for a in stage.inputs
            if a not in produced and not config.path(a).exists()
        ]
        if missing and not dry_run:
            raise StageError(
                f"Stage '{stage.name}' needs "
                f"{', '.join(str(config.path(a)) for a in missing)}, which "
                f"{'do' if len(missing) > 1 else 'does'} not exist. Run the earlier "
                f"stages first."
            )
        produced.update(stage.outputs)

    total = 0.0
    for stage in stages:
        total += run_stage(stage, config, dry_run=dry_run)

    if not dry_run:
        logger.info(
            "pipeline complete: %d stage(s) in %.1fs", len(stages), total
        )
        for artifact in dict.fromkeys(a for s in stages for a in s.outputs):
            logger.info("  %s -> %s", artifact, config.path(artifact))
