"""
Pipeline orchestrator: runs each engine stage to completion, in its own conda
environment, passing work between stages as files on disk.

Stages and their commands are declared in one pipeline-wide config (pipeline.yaml
at the repo root), not hardcoded here.

Author: Andrew LaPointe
Date: 2026-09-17
"""

from .config import (
    ConfigError,
    PipelineConfig,
    Stage,
    load_pipeline_config,
)
from .runner import StageError, run_pipeline, run_stage

__all__ = [
    "ConfigError",
    "PipelineConfig",
    "Stage",
    "StageError",
    "load_pipeline_config",
    "run_pipeline",
    "run_stage",
]
