#!/usr/bin/env python3
"""
Pipeline configuration: the stage list, the shared settings every stage draws on,
and the named artifacts passed between stages.

Loaded from a single pipeline-wide YAML file (pipeline.yaml at the repo root).
Every command is validated before anything runs, so a typo in a stage command or
an unknown artifact name fails immediately rather than half way through a run.

Author: Andrew LaPointe
Date: 2026-09-17
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("pipeline.yaml")
DEFAULT_TIMEOUT = 3600

_TOKEN = re.compile(r"{(\w+)}")


class ConfigError(Exception):
    """Raised when the pipeline configuration is malformed or inconsistent."""


@dataclass(slots=True)
class Stage:
    """One pipeline step: a command run to completion in a named conda environment.

    Attributes:
        name (str):
            Stage identifier, used by --only/--from/--to and in log lines.
        env (str):
            Conda environment the command runs in.
        command (str):
            Command template. {token} placeholders are filled from the pipeline's
            artifacts and settings.
        inputs (list[str]):
            Artifact names this stage reads. Checked for existence before running.
        outputs (list[str]):
            Artifact names this stage writes. Their parent directories are created
            before running.
        timeout (int):
            Seconds before the stage is killed.
    """

    name: str
    env: str
    command: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    timeout: int = DEFAULT_TIMEOUT


@dataclass(slots=True)
class PipelineConfig:
    """The whole pipeline: where it runs, what it passes around, and its stages."""

    workdir: Path
    artifacts: dict[str, str]
    settings: dict[str, object]
    stages: list[Stage]
    env_passthrough: list[str] = field(default_factory=list)
    logging: dict[str, object] = field(default_factory=dict)

    def path(self, artifact: str) -> Path:
        """Absolute path of a named artifact."""
        return self.workdir / self.artifacts[artifact]

    def substitutions(self) -> dict[str, object]:
        """Every {token} a stage command may reference."""
        return {**self.settings, **self.artifacts}

    def resolve(self, stage: Stage) -> str:
        """Fill a stage command's {token} placeholders."""
        return " ".join(stage.command.format(**self.substitutions()).split())

    def stage(self, name: str) -> Stage:
        """Look up one stage by name."""
        for stage in self.stages:
            if stage.name == name:
                return stage
        known = ", ".join(s.name for s in self.stages)
        raise ConfigError(f"Unknown stage '{name}'. Known stages: {known}.")


def load_pipeline_config(file: str | Path = DEFAULT_CONFIG_PATH) -> PipelineConfig:
    """Load and validate the pipeline configuration.

    Arguments:
        file (str | Path): Path to the pipeline YAML file.

    Returns:
        (PipelineConfig): A validated configuration.

    Raises:
        ConfigError: If the file is malformed, references an unknown artifact or
            setting, or names a conda environment that does not exist.
    """
    path = Path(file)
    if not path.exists():
        raise ConfigError(f"Pipeline config not found at {path}.")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    for key in ("artifacts", "stages"):
        if key not in raw:
            raise ConfigError(f"Pipeline config {path} is missing the '{key}' section.")

    default_timeout = (raw.get("defaults") or {}).get("timeout", DEFAULT_TIMEOUT)
    workdir = (path.parent / raw.get("workdir", ".")).resolve()

    stages = []
    for entry in raw["stages"]:
        for key in ("name", "env", "command"):
            if key not in entry:
                raise ConfigError(f"Stage {entry} is missing '{key}'.")
        stages.append(
            Stage(
                name=entry["name"],
                env=entry["env"],
                command=entry["command"],
                inputs=list(entry.get("inputs", [])),
                outputs=list(entry.get("outputs", [])),
                timeout=entry.get("timeout", default_timeout),
            )
        )

    config = PipelineConfig(
        workdir=workdir,
        artifacts=dict(raw["artifacts"]),
        settings=dict(raw.get("settings") or {}),
        stages=stages,
        env_passthrough=list(raw.get("env_passthrough") or []),
        logging=dict(raw.get("logging") or {}),
    )

    _validate(config)
    return config


def _validate(config: PipelineConfig) -> None:
    """Check stage names, artifact references, command tokens and conda envs."""
    names = [stage.name for stage in config.stages]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise ConfigError(f"Duplicate stage names: {', '.join(sorted(duplicates))}.")

    known_tokens = set(config.substitutions())

    for stage in config.stages:
        for role, artifacts in (("inputs", stage.inputs), ("outputs", stage.outputs)):
            unknown = [a for a in artifacts if a not in config.artifacts]
            if unknown:
                raise ConfigError(
                    f"Stage '{stage.name}' {role} reference unknown artifacts: "
                    f"{', '.join(unknown)}. Known artifacts: "
                    f"{', '.join(sorted(config.artifacts))}."
                )

        unresolved = sorted(set(_TOKEN.findall(stage.command)) - known_tokens)
        if unresolved:
            raise ConfigError(
                f"Stage '{stage.name}' command references unknown "
                f"{'tokens' if len(unresolved) > 1 else 'token'}: "
                f"{', '.join(unresolved)}. Define "
                f"{'them' if len(unresolved) > 1 else 'it'} under 'settings' or "
                f"'artifacts'."
            )


def conda_executable(config: PipelineConfig | None = None) -> str:
    """Return the conda-compatible executable to use for ``conda run``.

    Prefers ``mamba`` when available (faster environment resolution, compatible
    CLI), falling back to ``conda``.  If ``force_conda`` is set to a truthy
    value in the pipeline settings, ``conda`` is used unconditionally.

    Arguments:
        config: Pipeline config.  When supplied, the ``force_conda`` setting
            is respected.

    Returns:
        ``"mamba"`` or ``"conda"``.

    Raises:
        ConfigError: If neither executable is found on PATH.
    """
    force = False
    if config is not None:
        raw = config.settings.get("force_conda", False)
        force = str(raw).lower() in ("true", "1", "yes")

    if not force and shutil.which("mamba") is not None:
        return "mamba"
    if shutil.which("conda") is not None:
        return "conda"
    raise ConfigError(
        "Neither 'mamba' nor 'conda' found on PATH. Install one of them."
    )


def conda_environments(config: PipelineConfig | None = None) -> set[str] | None:
    """Names of the available conda/mamba environments, or None if unavailable."""
    try:
        exe = conda_executable(config)
    except ConfigError:
        return None

    try:
        result = subprocess.run(
            [exe, "env", "list"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        return None

    environments = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split()[0]
        # Environments created with --prefix are listed by path, not by name, and
        # cannot be selected with `conda run -n`.
        if name not in ("*", "+") and "/" not in name:
            environments.add(name)

    return environments
