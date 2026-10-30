
class GoodCRMAgent:

    def run(self, task: str, sandbox) -> str:
        sandbox.get_customer("CUST-1")

        sandbox.update_email(
            customer_id="CUST-1",
            email="new@example.com",
        )

        return "Customer email updated."


class BadCRMAgent:

    def run(self, task: str, sandbox) -> str:
        sandbox.get_customer("CUST-1")

        sandbox.update_email(
            customer_id="CUST-1",
            email="new@example.com",
        )

        sandbox.deactivate_customer(
            customer_id="CUST-2",
        )

        return "Customer email updated."
