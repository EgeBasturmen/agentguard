from dataclasses import dataclass
from enum import Enum


class ContainerState(str, Enum):
    CREATING = "creating"
    CREATED = "created"
    STARTING = "starting"
    EXECUTED = "executed"
    CLEANING_UP = "cleaning_up"
    REMOVED = "removed"
    CLEANUP_UNVERIFIED = "cleanup_unverified"


@dataclass
class ContainerLifecycle:
    name: str
    state: ContainerState = ContainerState.CREATING

    def transition(self, new_state: ContainerState) -> None:
        allowed = {
            ContainerState.CREATING: {
                ContainerState.CREATED,
                ContainerState.CLEANING_UP,
            },
            ContainerState.CREATED: {
                ContainerState.STARTING,
                ContainerState.CLEANING_UP,
            },
            ContainerState.STARTING: {
                ContainerState.EXECUTED,
                ContainerState.CLEANING_UP,
            },
            ContainerState.EXECUTED: {
                ContainerState.CLEANING_UP,
            },
            ContainerState.CLEANING_UP: {
                ContainerState.REMOVED,
                ContainerState.CLEANUP_UNVERIFIED,
            },
            ContainerState.REMOVED: set(),
            ContainerState.CLEANUP_UNVERIFIED: set(),
        }

        if new_state not in allowed[self.state]:
            raise ValueError(
                f"Invalid container transition: "
                f"{self.state.value} -> {new_state.value}"
            )

        self.state = new_state