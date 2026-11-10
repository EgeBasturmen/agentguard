from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


MutationType = Literal[
    "created",
    "updated",
    "deleted",
]


class StateMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str
    mutation_type: MutationType

    before: Any = None
    after: Any = None
