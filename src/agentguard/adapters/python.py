import importlib

from agentguard.adapters.base import AgentProtocol


class AgentLoadError(Exception):
    pass


def load_python_agent(
    reference: str,
) -> AgentProtocol:

    if ":" not in reference:
        raise AgentLoadError(
            "Agent reference must use "
            "'module.path:ClassName' format."
        )

    module_name, class_name = reference.split(
        ":",
        maxsplit=1,
    )

    if not module_name or not class_name:
        raise AgentLoadError(
            "Agent reference must use "
            "'module.path:ClassName' format."
        )

    try:
        module = importlib.import_module(
            module_name
        )

    except ImportError as exc:
        raise AgentLoadError(
            f"Could not import module "
            f"'{module_name}': {exc}"
        ) from exc

    try:
        agent_class = getattr(
            module,
            class_name,
        )

    except AttributeError as exc:
        raise AgentLoadError(
            f"Module '{module_name}' has no "
            f"agent class '{class_name}'."
        ) from exc

    try:
        agent = agent_class()

    except Exception as exc:
        raise AgentLoadError(
            f"Could not instantiate agent "
            f"'{reference}': {exc}"
        ) from exc

    run_method = getattr(
        agent,
        "run",
        None,
    )

    if not callable(run_method):
        raise AgentLoadError(
            f"Agent '{reference}' must provide "
            "a callable run(task, sandbox) method."
        )

    return agent