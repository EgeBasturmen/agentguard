
from copy import deepcopy
from typing import Any

from agentguard.sandbox.generic import GenericSandbox


class CRMSandbox(GenericSandbox):

    def __init__(self, initial_state, tracer):
        super().__init__(
            initial_state=initial_state,
            tracer=tracer,
        )

        self.register_tool(
            "customer.get",
            self._get_customer,
        )

        self.register_tool(
            "customer.update_email",
            self._update_email,
        )

        self.register_tool(
            "customer.deactivate",
            self._deactivate_customer,
        )

    @staticmethod
    def _get_customer(
        state: dict[str, Any],
        customer_id: str,
    ) -> dict[str, Any]:
        return deepcopy(
            state["customers"][customer_id]
        )

    @staticmethod
    def _update_email(
        state: dict[str, Any],
        customer_id: str,
        email: str,
    ) -> dict[str, Any]:
        customer = state["customers"][customer_id]
        customer["email"] = email

        return deepcopy(customer)

    @staticmethod
    def _deactivate_customer(
        state: dict[str, Any],
        customer_id: str,
    ) -> dict[str, Any]:
        customer = state["customers"][customer_id]
        customer["status"] = "inactive"

        return deepcopy(customer)

    def get_customer(self, customer_id: str):
        return self.call_tool(
            "customer.get",
            customer_id=customer_id,
        )

    def update_email(self, customer_id: str, email: str):
        return self.call_tool(
            "customer.update_email",
            customer_id=customer_id,
            email=email,
        )

    def deactivate_customer(self, customer_id: str):
        return self.call_tool(
            "customer.deactivate",
            customer_id=customer_id,
        )
