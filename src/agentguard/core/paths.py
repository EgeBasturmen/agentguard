"""Shared state paths: dotted keys, escaped dots/backslashes and '$' root."""

from typing import Any


class PathResolutionError(ValueError):
    """A state path cannot be resolved against the supplied value."""


def parse_path(path: str) -> tuple[str, ...]:
    # Empty paths occur internally when diffing the root, never in a spec.
    if path in ("", "$"):
        return ()
    parts: list[str] = []
    current: list[str] = []
    escaped = False
    for char in path:
        if escaped:
            if char not in ".\\$":
                raise ValueError("Only dots, backslashes and '$' may be escaped.")
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ".":
            if not current:
                raise ValueError("State paths cannot contain empty segments.")
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped or not current:
        raise ValueError("State path has an unfinished escape or empty segment.")
    parts.append("".join(current))
    return tuple(parts)


def join_path(parent: str, key: str) -> str:
    if not isinstance(key, str) or not key:
        raise ValueError("State diff requires non-empty string dictionary keys.")
    key = key.replace("\\", "\\\\").replace(".", "\\.").replace("$", "\\$")
    return f"{parent}.{key}" if parent and parent != "$" else key


def resolve_path(value: Any, parts: tuple[str, ...]) -> Any:
    for part in parts:
        if isinstance(value, dict):
            if part not in value:
                raise PathResolutionError(f"Dictionary key does not exist: {part!r}.")
            value = value[part]
        elif isinstance(value, list):
            if not part.isascii() or not part.isdecimal() or str(int(part)) != part:
                raise PathResolutionError(f"Invalid list index: {part!r}.")
            index = int(part)
            if index >= len(value):
                raise PathResolutionError(f"List index is out of range: {index}.")
            value = value[index]
        else:
            raise PathResolutionError(f"Cannot traverse {type(value).__name__} at {part!r}.")
    return value


def values_equal(before: Any, after: Any) -> bool:
    """State equality must not conflate booleans with integers, or int/float."""
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(
            values_equal(value, after[key]) for key, value in before.items()
        )
    if isinstance(before, list):
        return len(before) == len(after) and all(
            values_equal(left, right) for left, right in zip(before, after)
        )
    return before == after
