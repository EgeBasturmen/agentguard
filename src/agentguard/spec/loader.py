from pathlib import Path

import yaml
from pydantic import ValidationError

from agentguard.core.scenario import Scenario


class ScenarioLoadError(Exception):
    pass


def load_scenario(path: str | Path) -> Scenario:
    scenario_path = Path(path)

    if not scenario_path.exists():
        raise ScenarioLoadError(
            f"Scenario file not found: {scenario_path}"
        )

    if not scenario_path.is_file():
        raise ScenarioLoadError(
            f"Scenario path is not a file: {scenario_path}"
        )

    try:
        with scenario_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            raw = yaml.safe_load(file)

    except yaml.YAMLError as exc:
        raise ScenarioLoadError(
            f"Invalid YAML in {scenario_path}: {exc}"
        ) from exc

    if raw is None:
        raise ScenarioLoadError(
            f"Scenario file is empty: {scenario_path}"
        )

    if not isinstance(raw, dict):
        raise ScenarioLoadError(
            "Scenario YAML root must be an object."
        )

    try:
        return Scenario.model_validate(raw)

    except ValidationError as exc:
        raise ScenarioLoadError(
            f"Invalid scenario specification:\n{exc}"
        ) from exc