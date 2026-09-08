"""Check installed custom_nodes git repos for remote updates."""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .settings import load_settings, network_mode, proxy_url

PLUGIN_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_GIT_TIMEOUT_DEFAULT = 20
_GIT_TIMEOUT_PROXY = 15
_GIT_TIMEOUT_MIRROR = 18
_MAX_WORKERS = 8


@dataclass
class UpdateItem:
    name: str
    path: str
    remote_url: str = ""
    local_sha: str = ""
    remote_sha: str = ""
    branch: str = ""
    has_update: bool = False
    skipped: bool = False
    error: str = ""
    network_mode: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UpdateReport:
    status: str = "idle"  # idle | running | done | error
    message: str = ""
    started_at: str = ""
    finished_at: str = ""
    checked: int = 0
    skipped: int = 0
    error_count: int = 0
    update_count: int = 0
    network_mode: str = ""
    updates: list[UpdateItem] = field(default_factory=list)
    all_items: list[UpdateItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "checked": self.checked,
            "skipped": self.skipped,
            "error_count": self.error_count,
            "update_count": self.update_count,
            "network_mode": self.network_mode,
            "updates": [u.to_dict() for u in self.updates],
            "all_items": [u.to_dict() for u in self.all_items],
        }


UPDATE_REPORT = UpdateReport()
_LOCK = threading.Lock()
_STARTED = False


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _git_timeout(mode: str | None = None) -> int:
    mode = mode or network_mode()
    if mode == "proxy":
        return _GIT_TIMEOUT_PROXY
    if mode == "mirror":
        return _GIT_TIMEOUT_MIRROR
    return _GIT_TIMEOUT_DEFAULT


def _git_env(settings: dict[str, Any] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    # Avoid interactive prompts hanging the worker
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    px = proxy_url(settings)
    if px:
        env["http_proxy"] = px
        env["https_proxy"] = px
        env["HTTP_PROXY"] = px
        env["HTTPS_PROXY"] = px
        env["ALL_PROXY"] = px
        env["all_proxy"] = px
    else:
        # Ensure we don't accidentally inherit a broken system proxy when using mirror/direct
        for key in (
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "all_proxy",
        ):
            env.pop(key, None)
    return env


def _github_https_url(url: str) -> str:
    """Normalize to https://github.com/owner/repo[.git] form when possible."""
    url = (url or "").strip()
    if not url:
        return ""
    m = re.match(r"git@github\.com:(.+)$", url, re.I)
    if m:
        path = m.group(1)
        if not path.endswith(".git"):
            path += ".git"
        return f"https://github.com/{path}"
    m = re.match(r"ssh://git@github\.com/(.+)$", url, re.I)
    if m:
        path = m.group(1)
        if not path.endswith(".git"):
            path += ".git"
        return f"https://github.com/{path}"
    if "github.com" in url.lower():
        url = url.replace("git://github.com", "https://github.com")
        if url.startswith("http://github.com"):
            url = "https://" + url[len("http://") :]
        return url
    return url


def _apply_mirror(url: str, settings: dict[str, Any] | None = None) -> str:
    s = settings or load_settings()
    raw = _github_https_url(url)
    if not raw or "github.com" not in raw.lower():
        return url
    if network_mode(s) != "mirror":
        return raw
    prefix = str(s.get("mirror_prefix") or "").strip()
    if not prefix:
        return raw
    if not prefix.endswith("/"):
        prefix += "/"
    # Avoid double-wrapping
    if raw.startswith(prefix) or "ghfast.top" in raw or "ghproxy" in raw.lower():
        return raw
    return prefix + raw


def _network_fetch_url(origin_url: str, settings: dict[str, Any] | None = None) -> str:
    s = settings or load_settings()
    mode = network_mode(s)
    https = _github_https_url(origin_url) or origin_url
    if mode == "mirror":
        return _apply_mirror(https, s)
    return https


def _run_git(
    args: list[str],
    cwd: str,
    *,
    settings: dict[str, Any] | None = None,
    timeout: int | None = None,
) -> tuple[int, str, str]:
    s = settings or load_settings()
    mode = network_mode(s)
    to = timeout if timeout is not None else _git_timeout(mode)
    env = _git_env(s)
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=to,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"git timed out after {to}s ({mode})"
    except Exception as e:
        return 1, "", str(e)


def _short_sha(sha: str) -> str:
    sha = (sha or "").strip()
    return sha[:7] if len(sha) >= 7 else sha


def _normalize_remote_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    # git@github.com:owner/repo.git -> https://github.com/owner/repo
    m = re.match(r"git@([^:]+):(.+?)(?:\.git)?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2).rstrip('/')}"
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _is_github_url(url: str) -> bool:
    u = (url or "").lower()
    return "github.com" in u


def _custom_nodes_dirs() -> list[str]:
    dirs: list[str] = []
    try:
        import folder_paths  # type: ignore

        for p in folder_paths.get_folder_paths("custom_nodes") or []:
            if p and os.path.isdir(p):
                dirs.append(os.path.abspath(p))
    except Exception:
        pass

    parent = os.path.dirname(PLUGIN_ROOT)
    if parent and os.path.isdir(parent) and os.path.abspath(parent) not in dirs:
        # Only treat as custom_nodes if folder name matches or contains our siblings
        base = os.path.basename(parent).lower()
        if base == "custom_nodes" or os.path.isdir(os.path.join(parent, os.path.basename(PLUGIN_ROOT))):
            dirs.append(os.path.abspath(parent))

    # Dedupe
    out: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        key = os.path.normcase(os.path.realpath(d))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _iter_plugin_dirs() -> list[tuple[str, str]]:
    """Return list of (name, path) for installed custom node packages."""
    items: list[tuple[str, str]] = []
    for root in _custom_nodes_dirs():
        try:
            entries = sorted(os.listdir(root), key=str.lower)
        except OSError:
            continue
        for name in entries:
            if name.startswith(".") or name.startswith("__"):
                continue
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            # Skip disabled markers used by Manager
            if name.endswith(".disabled") or name.endswith(".bail"):
                continue
            items.append((name, path))
    return items


def _is_git_repo(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git"))


def check_one(name: str, path: str) -> UpdateItem:
    settings = load_settings()
    mode = network_mode(settings)
    item = UpdateItem(name=name, path=path, network_mode=mode)

    if not _is_git_repo(path):
        item.skipped = True
        item.error = "not a git repository"
        return item

    code, local_sha, err = _run_git(["rev-parse", "HEAD"], path, settings=settings)
    if code != 0 or not local_sha:
        item.error = err or "failed to read local HEAD"
        return item
    item.local_sha = local_sha

    code, remote_url, err = _run_git(["remote", "get-url", "origin"], path, settings=settings)
    if code != 0 or not remote_url:
        item.skipped = True
        item.error = err or "no origin remote"
        return item
    item.remote_url = _normalize_remote_url(remote_url)
    fetch_url = _network_fetch_url(remote_url, settings)

    # Prefer current branch remote tip; fall back to HEAD / main / master
    code, branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], path, settings=settings)
    branch = branch if code == 0 and branch and branch != "HEAD" else ""
    item.branch = branch

    remote_sha = ""
    candidates: list[str] = []
    if branch:
        candidates.append(f"refs/heads/{branch}")
    candidates.extend(["HEAD", "refs/heads/main", "refs/heads/master"])

    for ref in candidates:
        # Query rewritten URL directly so mirror/proxy applies even if origin is SSH.
        code, out, err = _run_git(["ls-remote", fetch_url, ref], path, settings=settings)
        if code != 0:
            item.error = err or f"ls-remote failed for {ref}"
            continue
        for line in out.splitlines():
            parts = line.split()
            if parts:
                remote_sha = parts[0].strip()
                break
        if remote_sha:
            item.error = ""
            break

    if not remote_sha:
        if not item.error:
            item.error = "could not resolve remote HEAD"
        return item

    item.remote_sha = remote_sha
    item.has_update = local_sha.lower() != remote_sha.lower()
    return item


def _set_report(report: UpdateReport) -> None:
    global UPDATE_REPORT
    with _LOCK:
        UPDATE_REPORT = report


def get_report() -> UpdateReport:
    with _LOCK:
        return UPDATE_REPORT


def run_update_check(*, force: bool = False) -> UpdateReport:
    """Scan installed plugins and compare local vs remote commits."""
    global _STARTED, UPDATE_REPORT
    with _LOCK:
        if UPDATE_REPORT.status == "running" and not force:
            return UPDATE_REPORT
        if UPDATE_REPORT.status == "done" and not force and _STARTED:
            return UPDATE_REPORT
        report = UpdateReport(status="running", message="checking", started_at=_now())
        UPDATE_REPORT = report
        _STARTED = True

    t0 = time.time()
    settings = load_settings()
    mode = network_mode(settings)
    plugins = _iter_plugin_dirs()
    items: list[UpdateItem] = []
    print(
        f"[QBB NodeOps] Update check network mode: {mode}"
        + (f" proxy={proxy_url(settings)}" if mode == "proxy" else "")
        + (f" mirror={settings.get('mirror_prefix')}" if mode == "mirror" else ""),
        flush=True,
    )

    try:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = {pool.submit(check_one, name, path): name for name, path in plugins}
            for fut in as_completed(futures):
                try:
                    items.append(fut.result())
                except Exception as e:
                    name = futures[fut]
                    items.append(UpdateItem(name=name, path="", error=str(e), network_mode=mode))

        items.sort(key=lambda x: x.name.lower())
        updates = [i for i in items if i.has_update]
        skipped = [i for i in items if i.skipped]
        errors = [i for i in items if i.error and not i.skipped and not i.has_update and not i.remote_sha]

        done = UpdateReport(
            status="done",
            message="ok",
            started_at=report.started_at,
            finished_at=_now(),
            checked=len(items) - len(skipped),
            skipped=len(skipped),
            error_count=len(errors),
            update_count=len(updates),
            network_mode=mode,
            updates=updates,
            all_items=items,
        )
        _set_report(done)

        elapsed = time.time() - t0
        print(
            f"[QBB NodeOps] Update check done in {elapsed:.1f}s: "
            f"{done.update_count} update(s), {done.checked} checked, "
            f"{done.skipped} skipped, {done.error_count} error(s)",
            flush=True,
        )
        if updates:
            print("[QBB NodeOps] Plugins with updates:", flush=True)
            for u in updates:
                print(
                    f"  - {u.name}: {_short_sha(u.local_sha)} -> {_short_sha(u.remote_sha)}"
                    + (f" ({u.remote_url})" if u.remote_url else ""),
                    flush=True,
                )
        return done
    except Exception as e:
        failed = UpdateReport(
            status="error",
            message=str(e),
            started_at=report.started_at,
            finished_at=_now(),
            all_items=items,
        )
        _set_report(failed)
        print(f"[QBB NodeOps] Update check failed: {e}", flush=True)
        return failed


def start_update_check_background(*, force: bool = False) -> None:
    """Fire-and-forget startup check (does not block ComfyUI)."""
    global UPDATE_REPORT
    with _LOCK:
        if UPDATE_REPORT.status == "running":
            return
        if UPDATE_REPORT.status == "done" and not force:
            return
        # Mark running immediately so UI polling sees progress
        UPDATE_REPORT = UpdateReport(
            status="running",
            message="checking",
            started_at=_now(),
        )

    def _worker():
        # Small delay so terminal summary / UI settle first
        time.sleep(1.5 if force else 2.0)
        run_update_check(force=True)

    threading.Thread(target=_worker, name="qbb-update-check", daemon=True).start()
    print("[QBB NodeOps] Background GitHub/git update check started", flush=True)


_PULL_LOCK = threading.Lock()
_PULLING = False


def _allowed_plugin_paths() -> set[str]:
    allowed: set[str] = set()
    for _name, path in _iter_plugin_dirs():
        try:
            allowed.add(os.path.normcase(os.path.realpath(path)))
        except OSError:
            continue
    return allowed


def _resolve_safe_plugin_path(path: str) -> tuple[bool, str]:
    path = os.path.abspath(os.path.expanduser((path or "").strip()))
    if not path or not os.path.isdir(path):
        return False, "plugin path not found"
    try:
        real = os.path.normcase(os.path.realpath(path))
    except OSError as e:
        return False, str(e)
    if real not in _allowed_plugin_paths():
        return False, "path is not under custom_nodes"
    if not _is_git_repo(path):
        return False, "not a git repository"
    return True, path


def pull_one(path: str) -> dict[str, Any]:
    """git fetch + pull --ff-only for one custom node package."""
    settings = load_settings()
    mode = network_mode(settings)
    ok, resolved = _resolve_safe_plugin_path(path)
    if not ok:
        return {"ok": False, "path": path, "name": os.path.basename(path or ""), "error": resolved}

    name = os.path.basename(resolved.rstrip("\\/"))
    before_code, before_sha, before_err = _run_git(["rev-parse", "HEAD"], resolved, settings=settings)
    if before_code != 0:
        return {
            "ok": False,
            "name": name,
            "path": resolved,
            "error": before_err or "failed to read local HEAD",
        }

    code, origin_url, _ = _run_git(["remote", "get-url", "origin"], resolved, settings=settings)
    fetch_url = _network_fetch_url(origin_url, settings) if code == 0 and origin_url else "origin"

    code, branch, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], resolved, settings=settings)
    branch = branch if code == 0 and branch and branch != "HEAD" else ""

    # Prefer fetch from mirrored/proxy URL, then ff-only merge
    if branch:
        fetch_code, _out, fetch_err = _run_git(
            ["fetch", "--prune", fetch_url, branch],
            resolved,
            settings=settings,
        )
    else:
        fetch_code, _out, fetch_err = _run_git(
            ["fetch", "--prune", fetch_url],
            resolved,
            settings=settings,
        )

    if fetch_code != 0:
        # Fallback to plain origin (still with proxy env if enabled)
        fetch_code2, _out2, fetch_err2 = _run_git(
            ["fetch", "--prune", "origin"],
            resolved,
            settings=settings,
        )
        if fetch_code2 != 0:
            return {
                "ok": False,
                "name": name,
                "path": resolved,
                "local_sha": before_sha,
                "network_mode": mode,
                "error": fetch_err2 or fetch_err or "git fetch failed",
            }

    pull_args = ["pull", "--ff-only"]
    if fetch_url != "origin" and branch:
        # Already fetched tip; merge FETCH_HEAD when possible
        merge_code, merge_out, merge_err = _run_git(
            ["merge", "--ff-only", "FETCH_HEAD"],
            resolved,
            settings=settings,
        )
        if merge_code == 0:
            pull_out, pull_err = merge_out, merge_err
        else:
            pull_args = ["pull", "--ff-only", "origin"]
            if branch:
                pull_args.append(branch)
            pull_code, pull_out, pull_err = _run_git(pull_args, resolved, settings=settings)
            if pull_code != 0:
                return {
                    "ok": False,
                    "name": name,
                    "path": resolved,
                    "local_sha": before_sha,
                    "branch": branch,
                    "network_mode": mode,
                    "error": pull_err or merge_err or "git pull --ff-only failed",
                    "detail": (pull_out or merge_out or "").strip(),
                }
    else:
        if branch:
            pull_args.extend(["origin", branch])
        pull_code, pull_out, pull_err = _run_git(pull_args, resolved, settings=settings)
        if pull_code != 0:
            pull_code2, pull_out2, pull_err2 = _run_git(
                ["pull", "--ff-only"],
                resolved,
                settings=settings,
            )
            if pull_code2 != 0:
                return {
                    "ok": False,
                    "name": name,
                    "path": resolved,
                    "local_sha": before_sha,
                    "branch": branch,
                    "network_mode": mode,
                    "error": pull_err2 or pull_err or "git pull --ff-only failed",
                    "detail": (pull_out2 or pull_out or "").strip(),
                }
            pull_out, pull_err = pull_out2, pull_err2

    after_code, after_sha, after_err = _run_git(["rev-parse", "HEAD"], resolved, settings=settings)
    if after_code != 0:
        return {
            "ok": False,
            "name": name,
            "path": resolved,
            "local_sha": before_sha,
            "error": after_err or "failed to read HEAD after pull",
        }

    changed = before_sha.lower() != after_sha.lower()
    print(
        f"[QBB NodeOps] Updated {name}: "
        f"{_short_sha(before_sha)} -> {_short_sha(after_sha)}"
        + ("" if changed else " (already up to date)")
        + f" [{mode}]",
        flush=True,
    )
    return {
        "ok": True,
        "name": name,
        "path": resolved,
        "branch": branch,
        "local_sha_before": before_sha,
        "local_sha": after_sha,
        "changed": changed,
        "network_mode": mode,
        "message": (pull_out or pull_err or ("updated" if changed else "already up to date")).strip(),
    }


def pull_many(paths: list[str] | None = None) -> dict[str, Any]:
    """Pull selected paths, or all currently outdated plugins if paths is None/empty."""
    global _PULLING
    with _PULL_LOCK:
        if _PULLING:
            return {"ok": False, "error": "another update is already running", "results": []}
        _PULLING = True

    try:
        targets: list[str] = []
        if paths:
            targets = [str(p).strip() for p in paths if str(p).strip()]
        else:
            report = get_report()
            targets = [u.path for u in report.updates if u.path]

        if not targets:
            return {"ok": True, "updated": 0, "failed": 0, "results": [], "message": "nothing to update"}

        results: list[dict[str, Any]] = []
        # Sequential pulls avoid git lock / network storms
        for path in targets:
            results.append(pull_one(path))

        updated = sum(1 for r in results if r.get("ok") and r.get("changed"))
        ok_count = sum(1 for r in results if r.get("ok"))
        failed = sum(1 for r in results if not r.get("ok"))

        # Refresh outdated list after pulls
        try:
            run_update_check(force=True)
        except Exception as e:
            print(f"[QBB NodeOps] recheck after pull failed: {e}", flush=True)

        return {
            "ok": failed == 0,
            "updated": updated,
            "succeeded": ok_count,
            "failed": failed,
            "results": results,
            "report": get_report().to_dict(),
        }
    finally:
        with _PULL_LOCK:
            _PULLING = False


def is_pulling() -> bool:
    with _PULL_LOCK:
        return _PULLING
