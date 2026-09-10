"""Validation helpers for deployment-provided worker configuration."""

import os


def env_bool(name: str, default: bool = False) -> bool:
    """Read a strict boolean environment switch."""
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off."
    )


def required_deployment_value(name: str) -> str:
    """Return a configured value or fail before a deployment SDK is invoked."""
    value = os.getenv(name, "").strip()
    normalized = value.casefold()
    placeholder_markers = (
        "placeholder",
        "replace-me",
        "changeme",
        "your-",
        "your_",
    )
    if (
        not value
        or normalized.startswith("@microsoft.keyvault")
        or "<" in value
        or ">" in value
        or any(marker in normalized for marker in placeholder_markers)
    ):
        raise RuntimeError(
            f"{name} must be set to a concrete deployment value "
            "before starting the LiveKit worker."
        )
    return value
