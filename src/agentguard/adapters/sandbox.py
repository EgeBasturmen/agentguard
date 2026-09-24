import importlib
import inspect

from agentguard.sandbox.base import SandboxFactory


class SandboxLoadError(Exception):
    pass


def load_python_sandbox(
    reference: str,
) -> SandboxFactory:

    if reference.count(":") != 1:
        raise SandboxLoadError(
            "Sandbox reference must use "
            "'module.path:ClassName' format."
        )

    module_name, class_name = reference.split(":", 1)

    if not module_name or not class_name:
        raise SandboxLoadError(
            "Sandbox reference must use "
            "'module.path:ClassName' format."
        )

    try:
        module = importlib.import_module(module_name)

    except ImportError as exc:
        raise SandboxLoadError(
            f"Could not import sandbox module "
            f"'{module_name}': {exc}"
        ) from exc

    try:
        sandbox_class = getattr(module, class_name)

    except AttributeError as exc:
        raise SandboxLoadError(
            f"Module '{module_name}' has no "
            f"sandbox class '{class_name}'."
        ) from exc

    if not inspect.isclass(sandbox_class):
        raise SandboxLoadError(
            f"Sandbox '{reference}' must be a class."
        )

    try:
        signature = inspect.signature(sandbox_class)
        signature.bind(
            initial_state={},
            tracer=None,
        )

    except (TypeError, ValueError) as exc:
        raise SandboxLoadError(
            f"Sandbox '{reference}' must accept "
            "initial_state and tracer arguments."
        ) from exc

    get_state = getattr(sandbox_class, "get_state", None)

    if not callable(get_state):
        raise SandboxLoadError(
            f"Sandbox '{reference}' must provide "
            "a callable get_state() method."
        )

    return sandbox_class