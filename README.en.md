# QBB NodeOps for ComfyUI

**Diagnose custom nodes, manage resources, save environment records, and maintain multiple ComfyUI instances from one panel.**

[中文](README.md) · [Usage guide (Chinese)](docs/usage.md) · [Changes](CHANGELOG.md) · [Report an issue](https://github.com/rhinoflavored/ComfyUI_QBB_NodeOps/issues)

QBB NodeOps adds a web management panel and startup diagnostics. Its Chinese/English interface follows ComfyUI's language setting. It adds no workflow nodes. Previously named **ComfyUI_QBB_LoadHighlight**.

## Features

- Loading reports, tracebacks, Git update checks and fast-forward updates, log and dependency exports.
- Plugin/model storage usage, file dates, recorded workflow references, and recycling selected resources.
- Record previews, capture, comparison, import/export, and reviewed restoration of the current instance.
- Independent instance creation with Python/PyTorch selection and local CPU/CUDA checks.
- Plugin migration into existing instances and optional recycling after verification and confirmation.
- Public plugin catalog search, categories, tags, repository information, and Git downloads.
- Optional, user-triggered analysis through a local or remote OpenAI-compatible service.

## Installation

Requires working ComfyUI, **Python 3.11+**, and system **Git** for repository operations. Windows is the primary development and validation platform; other systems and plugin combinations require verification.

Run in `ComfyUI/custom_nodes`:

```bash
git clone https://github.com/rhinoflavored/ComfyUI_QBB_NodeOps.git
```

Install requirements with the Python interpreter that runs ComfyUI. For Windows portable installations, run from the directory containing `python_embeded` and `ComfyUI`:

```powershell
.\python_embeded\python.exe -m pip install -r .\ComfyUI\custom_nodes\ComfyUI_QBB_NodeOps\requirements.txt
```

For other installations, use your ComfyUI Python with `-m pip install -r <plugin directory>/requirements.txt`. The additional dependency is `Send2Trash`.

Restart ComfyUI, refresh the browser, and open the toolbar entry or **QBB → NodeOps**. Access through `localhost` or `127.0.0.1` on the ComfyUI host; management endpoints restrict requests to the local machine. Keep only one installation when upgrading from LoadHighlight.

## Records and restoration

Managed scope: **ComfyUI core, plugins, Python package state, and `extra_model_paths.yaml`**. Models, inputs, outputs, workflows, `user`, and external resources are excluded. NodeOps protects itself. Records are not full-machine backups.

Pause workflows and environment changes, preview coverage and gaps, then confirm capture. Local content and readable installed package files can be stored in verified component ZIPs, with SHA-256 object reuse. Lightweight Git records may contain only source and commit information.

Comparison defaults to **loose mode**, which compares component names and skips matching names. Select **strict mode** to inspect versions, sources, and internal files. Review replacements and removals, check materials, prepare, and confirm restoration.

Restoration opens a separate worker window. **Close ComfyUI manually**, wait for that window to report completion, then **restart ComfyUI manually** using your usual launcher. Execution follows the approved plan. Completion may mean actions were applied without rescanning the environment; it does not establish file, GPU, or workflow equivalence. Run a new strict comparison and test workflows when verification is needed. Backup gaps, ownership conflicts, and incompatible interpreters can block execution.

Legacy records retain reading, comparison, and export; restoration depends on their saved evidence and materials.

## Practical limits

Missing workflow references do not prove a resource is unused. Partial-load detection is heuristic. Migration changes target dependencies and does not move models or core. AI requests can include logs, paths, and package information; keys are stored locally without encryption. Review exports before sharing.

Update with `git pull --ff-only`, install requirements using ComfyUI's Python, restart, and refresh. Preserve local modifications first. Report reproducible steps, ComfyUI/Python versions, and errors with secrets removed. Do not upload local settings, databases, or entire backup stores.

Licensed under [MIT](LICENSE).
