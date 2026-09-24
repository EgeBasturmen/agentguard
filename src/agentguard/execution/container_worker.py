import json
import sys


PROTOCOL_VERSION = 1

MAX_REQUEST_BYTES = 64 * 1024
MAX_SCENARIO_NAME_LENGTH = 200


def main() -> None:
    raw_request = sys.stdin.buffer.readline(
        MAX_REQUEST_BYTES + 1
    )

    if len(raw_request) > MAX_REQUEST_BYTES:
        raise ValueError("Request exceeds size limit.")

    request = json.loads(
        raw_request.decode("utf-8")
    )

    if not isinstance(request, dict):
        raise ValueError(
            "Request must be a JSON object."
        )

    if set(request) != {
        "protocol_version",
        "scenario_name",
    }:
        raise ValueError(
            "Unexpected request fields."
        )

    if (
        type(request["protocol_version"]) is not int
        or request["protocol_version"] != PROTOCOL_VERSION
    ):
        raise ValueError(
            "Unsupported protocol version."
        )

    scenario_name = request["scenario_name"]

    if (
        not isinstance(scenario_name, str)
        or not 1 <= len(scenario_name) <= MAX_SCENARIO_NAME_LENGTH
    ):
        raise ValueError(
            "Invalid scenario name."
        )

    response = {
        "protocol_version": PROTOCOL_VERSION,
        "status": "ready",
        "scenario_name": scenario_name,
        "message": (
            "Container worker received the request."
        ),
    }

    sys.stdout.write(
        json.dumps(response) + "\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()