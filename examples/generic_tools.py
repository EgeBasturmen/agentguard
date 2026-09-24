from typing import Any


def read_document(
    state: dict[str, Any],
    document_id: str,
) -> str:
    return state["documents"][document_id]["content"]


def delete_document(
    state: dict[str, Any],
    document_id: str,
) -> None:
    del state["documents"][document_id]