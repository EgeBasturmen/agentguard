from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.sandbox.crm import CRMSandbox


SANDBOX_REGISTRY = {
    "commerce": CommerceSandbox,
    "crm": CRMSandbox,
}


def get_sandbox_factory(name: str):
    try:
        return SANDBOX_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(SANDBOX_REGISTRY))
        raise ValueError(
            f"Unknown sandbox '{name}'. "
            f"Available sandboxes: {available}"
        ) from None