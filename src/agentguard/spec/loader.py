from pathlib import Path

import yaml
from pydantic import ValidationError

from agentguard.core.scenario import Scenario


class ScenarioLoadError(Exception):
    pass


class ScenarioYAMLLoader(yaml.SafeLoader):
    """Do not silently discard earlier checks or fields with duplicate keys."""


def _unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    None, None, f"Duplicate YAML key: {key!r}", key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                None, None, "YAML mapping keys must be hashable.", key_node.start_mark,
            ) from exc
    return mapping


ScenarioYAMLLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping,
)


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
            raw = yaml.load(file, Loader=ScenarioYAMLLoader)

    except yaml.YAMLError as exc:
        raise ScenarioLoadError(
            f"Invalid YAML in {scenario_path}: {exc}"
        ) from exc

    except (OSError, UnicodeError) as exc:
        raise ScenarioLoadError(
            f"Could not read scenario file {scenario_path}: {exc}"
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
