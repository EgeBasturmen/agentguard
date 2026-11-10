
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    schema: type[BaseModel]
    handler: Callable[..., Any]
    stateful: bool = False


class ToolRegistry:
    """
    Trusted registry of explicitly allowed tools.

    Registration must happen in the trusted controller,
    never through untrusted agent requests.
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        schema: type[BaseModel],
        handler: Callable[..., Any],
        *, stateful: bool = False,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be non-empty.")

        if not isinstance(schema, type) or not issubclass(
            schema, BaseModel
        ):
            raise TypeError("Schema must be a Pydantic model.")

        if not callable(handler):
            raise TypeError("Handler must be callable.")

        if name in self._tools:
            raise ValueError(
                f"Tool already registered: {name}"
            )

        self._tools[name] = RegisteredTool(
            name=name,
            schema=schema,
            handler=handler,
            stateful=stateful,
        )

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())
