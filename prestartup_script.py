"""
ComfyUI runs every custom_nodes/*/prestartup_script.py BEFORE importing `nodes`
and BEFORE loading custom node packages.

We install an import hook here so that as soon as `nodes` is imported by ComfyUI,
`nodes.load_custom_node` is patched. This does NOT depend on this folder's name
or filesystem sort order.
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from qbb_nodeops.settings import apply_comfyui_process_proxy, load_settings  # noqa: E402
from qbb_nodeops.hook import install  # noqa: E402

# This runs before `nodes` and all custom nodes are imported, so a saved local
# proxy is inherited by the whole ComfyUI process and later subprocesses.
if apply_comfyui_process_proxy(load_settings()):
    print("[QBB NodeOps] Applied saved proxy to the ComfyUI process", flush=True)

install()
