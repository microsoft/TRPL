# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Load local.settings.json Values into environment (dev convenience helper)."""
import json
from pathlib import Path


def load_local_settings(caller_file: str = __file__) -> None:
    """Load ``local.settings.json`` Values into ``os.environ`` (setdefault).

    Looks for the file in the same directory as *caller_file*, which should be
    ``__file__`` from the calling module.  Has no effect when the file is absent
    (production deployments never ship it).
    """
    import os  # pylint: disable=import-outside-toplevel
    settings_path = Path(caller_file).resolve().parent / "local.settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        for k, v in data.get("Values", {}).items():
            os.environ.setdefault(k, v)
