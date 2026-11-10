from typing import Any

from agentguard.core.mutation import StateMutation
from agentguard.core.paths import join_path, values_equal


def diff_states(
    before: Any,
    after: Any,
    path: str = "",
) -> list[StateMutation]:

    mutations: list[StateMutation] = []

    if isinstance(before, dict) and isinstance(after, dict):

        all_keys = set(before) | set(after)

        if any(not isinstance(key, str) or not key for key in all_keys):
            raise ValueError("State diff requires non-empty string dictionary keys.")
        for key in sorted(all_keys):

            current_path = join_path(path, key)

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

        if not values_equal(before, after):

            mutations.append(
                StateMutation(
                    path=path,
                    mutation_type="updated",
                    before=before,
                    after=after,
                )
            )

        return mutations

    if not values_equal(before, after):

        mutations.append(
            StateMutation(
                path=path,
                mutation_type="updated",
                before=before,
                after=after,
            )
        )

    return mutations
