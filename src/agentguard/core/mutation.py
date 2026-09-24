from typing import Any, Literal

from pydantic import BaseModel


MutationType = Literal[
    "created",
    "updated",
    "deleted",
]


class StateMutation(BaseModel):
    path: str
    mutation_type: MutationType

    before: Any = None
    after: Any = None