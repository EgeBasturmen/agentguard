from copy import deepcopy
from typing import Any

from pydantic import BaseModel


class StateSnapshot(BaseModel):
    state: dict[str, Any]

    @classmethod
    def capture(cls, state: dict[str, Any]) -> "StateSnapshot":
        return cls(
            state=deepcopy(state)
        )