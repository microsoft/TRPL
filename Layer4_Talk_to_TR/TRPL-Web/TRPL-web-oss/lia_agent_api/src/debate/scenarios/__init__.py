# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

__all__ = [
    "ScenarioDefinition",
    "get_scenario",
    "get_scenario_context",
    "SCENARIOS",
]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)

    from debate.scenarios import registry

    return getattr(registry, name)
