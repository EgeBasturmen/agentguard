"""Versioned comparison identity; hashes are reproducibility metadata, not signatures."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentguard.core.paths import parse_path
from agentguard.core.scenario import Scenario

EVALUATOR_VERSION = "state-evaluators-v1"


def canonical_json(value: Any) -> str:
    def validate(item: Any) -> None:
        if isinstance(item, dict):
            if any(type(key) is not str for key in item):
                raise ValueError("Comparison manifests require string JSON keys.")
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("Comparison manifests require JSON-compatible values.")
    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def check_id(kind: str, selector: Any) -> str:
    return digest({"kind": kind, "selector": selector})


class ComparisonManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scenario: Scenario
    environment: str = Field(min_length=1)
    evaluator_version: str = Field(default=EVALUATOR_VERSION, min_length=1)
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    def check_definitions(self) -> dict[str, dict[str, Any]]:
        checks = {}
        groups = (
            ("expected", self.scenario.expected_state),
            ("unchanged", self.scenario.forbidden_changes),
            ("tool", self.scenario.tool_expectations),
            ("invariant", self.scenario.invariants),
        )
        for kind, items in groups:
            for item in items:
                definition = item.model_dump()
                if kind in ("expected", "unchanged"):
                    selector = list(parse_path(item.path))
                    definition["path"] = selector
                else:
                    selector = item.tool_name if kind == "tool" else item.name
                checks[check_id(kind, selector)] = {"kind": kind, **definition}
        for item in self.scenario.evaluations:
            checks[check_id('evaluation',item.id)]={'kind':'evaluation',**item.model_dump()}
        return checks

    @model_validator(mode="after")
    def verify_fingerprint(self) -> "ComparisonManifest":
        payload = {
            "manifest_version": 1,
            "scenario_name": self.scenario.name,
            "task": self.scenario.task,
            "initial_state": self.scenario.initial_state,
            "checks": self.check_definitions(),
            "environment": self.environment,
            "evaluator_version": self.evaluator_version,
        }
        if self.scenario.turns:payload['turns']=[turn.model_dump() for turn in self.scenario.turns]
        if self.scenario.faults:payload['faults']=[fault.model_dump() for fault in self.scenario.faults]
        expected = digest(payload)
        if self.fingerprint is not None and self.fingerprint != expected:
            raise ValueError("Comparison manifest fingerprint does not match its content.")
        self.fingerprint = expected
        return self
