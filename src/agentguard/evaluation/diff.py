from typing import Any

from agentguard.core.mutation import StateMutation


def diff_states(
    before: Any,
    after: Any,
    path: str = "",
) -> list[StateMutation]:

    mutations: list[StateMutation] = []

    if isinstance(before, dict) and isinstance(after, dict):

        all_keys = set(before) | set(after)

        for key in all_keys:

            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if key not in before:

                mutations.append(
                    StateMutation(
                        path=current_path,
                        mutation_type="created",
                        before=None,
                        after=after[key],
                    )
                )

                continue

            if key not in after:

                mutations.append(
                    StateMutation(
                        path=current_path,
                        mutation_type="deleted",
                        before=before[key],
                        after=None,
                    )
                )

                continue

            mutations.extend(
                diff_states(
                    before[key],
                    after[key],
                    current_path,
                )
            )

        return mutations

    if isinstance(before, list) and isinstance(after, list):

        if before != after:

            mutations.append(
                StateMutation(
                    path=path,
                    mutation_type="updated",
                    before=before,
                    after=after,
                )
            )

        return mutations

    if before != after:

        mutations.append(
            StateMutation(
                path=path,
                mutation_type="updated",
                before=before,
                after=after,
            )
        )

    return mutations