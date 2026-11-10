"""Strict run-report loading with explicit legacy handling."""

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentguard.core.outcome import effective_pass
from agentguard.core.result import RunResult

REPORT_SCHEMA_VERSION = 2
MAX_REPORT_BYTES = 16 * 1024 * 1024


class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[2] = REPORT_SCHEMA_VERSION
    agent: str = Field(min_length=1)
    verdict: Literal["PASS", "FAIL"]
    result: RunResult

    @model_validator(mode="after")
    def verify_result(self) -> "RunReport":
        expected = "PASS" if effective_pass(self.result) else "FAIL"
        if self.verdict != expected:
            raise ValueError("Stored verdict contradicts the evaluated run; rerun legacy reports without evaluation evidence.")
        names = [item.name for item in self.result.assertions]
        ids = [item.assertion_id for item in self.result.assertions if item.assertion_id is not None]
        paths = [item.path for item in self.result.mutations]
        if len(names) != len(set(names)) or len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("Report contains duplicate assertion identities or mutation paths.")
        return self

    @classmethod
    def from_result(cls, result: RunResult, agent: str) -> "RunReport":
        # Validate mutable instances instead of trusting their current Python types.
        checked = result.validated_copy()
        return cls(agent=agent, verdict="PASS" if effective_pass(checked) else "FAIL", result=checked)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate report JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number in report: {value}")


def validate_report(payload: RunReport | dict[str, Any]) -> RunReport:
    if isinstance(payload, RunReport):
        payload = payload.model_dump(mode="json")
    if not isinstance(payload, dict):
        raise ValueError("Report root must be an object.")
    version = payload.get("schema_version")
    if type(version) is not int or version not in (1, REPORT_SCHEMA_VERSION):
        raise ValueError(f"Unsupported report schema version: {version!r}")
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("comparison_manifest"), dict):
        if not result["comparison_manifest"].get("fingerprint"):
            raise ValueError("A serialized comparison manifest must include its fingerprint.")
    # v1 already contains RunResult. Missing evaluation/manifest fields retain
    # their fail-closed defaults; never infer them from a scenario name/verdict.
    migrated = {**payload, "schema_version": REPORT_SCHEMA_VERSION}
    return RunReport.model_validate_json(json.dumps(migrated, allow_nan=False))


def load_report(path: str | Path) -> RunReport:
    with Path(path).open("rb") as source:
        raw = source.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds maximum allowed size.")
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except UnicodeError as exc:
        raise ValueError("Report is not valid Unicode JSON.") from exc
    return validate_report(payload)
