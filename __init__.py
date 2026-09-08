"""QBB NodeOps — highlight custom node loading in terminal + failure popup."""

from __future__ import annotations

import os
import sys

WEB_DIRECTORY = "./web"

# No workflow nodes; this extension is diagnostic UI + startup hook only.
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from qbb_nodeops.hook import install  # noqa: E402
from qbb_nodeops.routes import register_routes  # noqa: E402

# Idempotent: normally already installed via prestartup_script.py
install()
register_routes()

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
