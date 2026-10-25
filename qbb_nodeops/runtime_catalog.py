"""Discover stable runtime combinations from upstream release and wheel metadata."""

from concurrent.futures import ThreadPoolExecutor
from html import unescape
import json
import os
from pathlib import Path
import platform
import re
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError
from .settings import load_settings, proxy_url, network_mode

RELEASES = "https://pytorch.org/get-started/previous-versions/"
PYTHONS = "https://raw.githubusercontent.com/astral-sh/uv/refs/heads/main/crates/uv-python-managed/download-metadata.json"
PYTHON_SOURCES = (PYTHONS, "https://github.com/astral-sh/uv/raw/refs/heads/main/crates/uv-python-managed/download-metadata.json")
CACHE_DIR = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'QBBNodeOps' / 'runtime-catalog'
_cache = {}
_lock = threading.Lock()


def fetch(url, refresh=False):
    settings = load_settings()
    proxy = proxy_url(settings)
    mode = network_mode(settings)
    request_url = url
    if mode == 'mirror' and urlsplit(url).hostname in ('github.com', 'raw.githubusercontent.com'):
        request_url = settings['mirror_prefix'].rstrip('/') + '/' + url
    key = (url, request_url, proxy)
    with _lock:
        cached = _cache.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy} if proxy else {}))
    try:
        with opener.open(Request(request_url, headers={"User-Agent": "QBB-NodeOps"}), timeout=25) as response:
            text = response.read(20_000_001)
    except Exception as exc:
        if isinstance(exc, HTTPError):
            hint = '下载地址返回 404；不能据此判断代理端口错误。' if exc.code == 404 else f'下载服务器返回 HTTP {exc.code}，请稍后刷新。'
        else:
            hint = '仅 GitHub 镜像不能代理 PyTorch/PyPI；请配置可用的本地代理或确认这些站点可直连。' if mode == 'mirror' else '请检查网络连接与代理设置后刷新。'
        raise RuntimeError(f'版本源 {url} 读取失败（{mode}）：{exc}。{hint}') from exc
    if len(text) > 20_000_000:
        raise ValueError("Version metadata exceeded size limit")
    text = text.decode("utf-8")
    with _lock:
        _cache[key] = (time.monotonic(), text)
    return text


def version_key(value):
    return tuple(int(part) for part in value.split("."))


def parse_releases(html):
    text = unescape(re.sub(r"<[^>]+>", "", html))
    releases = {}
    for line in text.splitlines():
        build = re.search(r"--index-url\s+https://download.pytorch.org/whl/(cpu|cu[0-9]+)(?:\s|$)", line.strip())
        pins = dict(re.findall(r"\b(torch|torchvision|torchaudio)==([0-9]+\.[0-9]+\.[0-9]+)(?![0-9a-z.])", line))
        if build and "torch" in pins and "torchvision" in pins:
            releases[(pins["torch"], build[1])] = pins
    if not releases:
        raise ValueError("Cannot parse official PyTorch releases; please retry after updating NodeOps")
    return releases


def python_versions(metadata):
    system = platform.system().lower()
    versions = set()
    for item in metadata.values():
        if (item.get("name") == "cpython" and item.get("os") == system
                and item.get("arch", {}).get("family") == "x86_64"
                and not item.get("arch", {}).get("variant")
                and not item.get("prerelease") and not item.get("variant")
                and (system != "linux" or item.get("libc") == "gnu")):
            versions.add(f"{item['major']}.{item['minor']}")
    return versions


def available_python_versions(refresh=False):
    errors = []
    for source in PYTHON_SOURCES:
        try:
            versions = python_versions(json.loads(fetch(source, refresh)))
            if not versions:
                raise ValueError('清单中没有适合本机的稳定 Python 版本')
            return versions
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError) as exc:
            errors.append(str(exc))
    raise RuntimeError('Python 版本清单的主入口和备用入口均失败：' + '；'.join(errors))


def catalog_cache_path(build):
    # Build is supplied by the request; never use it as an arbitrary path.
    if not re.fullmatch(r'cpu|cu[0-9]+', build):
        raise ValueError('Unknown CPU/CUDA build')
    return CACHE_DIR / f'{platform.system()}-{platform.machine()}-{build}.json'


def save_catalog_cache(build, result):
    if not result.get('combinations'):
        return
    try:
        path = catalog_cache_path(build)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({'saved_at': time.time(), 'data': result}), encoding='utf-8')
        temp.replace(path)
    except OSError:
        pass  # A read-only cache must not prevent runtime discovery.


def read_catalog_cache(build):
    try:
        path = catalog_cache_path(build)
        if path.stat().st_size > 20_000_000:
            return None
        stored = json.loads(path.read_text(encoding='utf-8'))
        age = time.time() - stored['saved_at']
        data = stored['data']
        if not 0 <= age <= 7 * 86400 or not data['combinations'] or build not in data['builds']:
            return None
        if any(row['build'] != build or not row['python'] or not row['torch'] or not row['pins'] for row in data['combinations']):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return None


def wheel_versions(html, package):
    result = {}
    for filename in re.findall(r'href=["\']([^"\']+)', html):
        name = unquote(filename).split("#")[0].rsplit("/", 1)[-1]
        match = re.fullmatch(package + r"-([0-9]+\.[0-9]+\.[0-9]+(?:\+[a-z0-9.]+)?)-cp(3[0-9]+)-cp\2-([^.]+)\.whl", name)
        if not match:
            continue
        wheel_platform = match[3]
        if platform.system() == "Windows":
            if wheel_platform != "win_amd64":
                continue
        else:
            if not wheel_platform.endswith("_x86_64"):
                continue
            glibc = re.match(r"manylinux_([0-9]+)_([0-9]+)_", wheel_platform)
            if glibc and version_key(platform.libc_ver()[1] or "0.0") < (int(glibc[1]), int(glibc[2])):
                continue
            if not wheel_platform.startswith(("manylinux", "linux_")):
                continue
        python = match[2][0] + "." + match[2][1:]
        result[(match[1].split("+")[0], python)] = match[1]
    return result


def _collect_catalog(build="cpu", refresh=False):
    if platform.system() not in ("Windows", "Linux") or platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("Runtime catalog currently supports Windows/Linux x64")
    releases = parse_releases(fetch(RELEASES, refresh))
    builds = sorted({key[1] for key in releases}, key=lambda x: (x != "cpu", int(x[2:]) if x != "cpu" else 0))
    if build not in builds:
        raise ValueError("Unknown CPU/CUDA build")
    packages = {name for (_, channel), pins in releases.items() if channel == build for name in pins}
    with ThreadPoolExecutor(max_workers=4) as pool:
        py_future = pool.submit(available_python_versions, refresh)
        futures = {name: pool.submit(fetch, f"https://download.pytorch.org/whl/{build}/{name}/", refresh)
                   for name in packages}
        available_python = py_future.result()
        wheels = {name: wheel_versions(future.result(), name) for name, future in futures.items()}
    rows = []
    for (torch, channel), pins in releases.items():
        if channel != build:
            continue
        for python in available_python:
            if all((version, python) in wheels[name] for name, version in pins.items()):
                rows.append({"python": python, "torch": torch, "build": build,
                             "pins": {name: wheels[name][(version, python)] for name, version in pins.items()}})
    rows.sort(key=lambda row: (version_key(row["torch"]), version_key(row["python"])), reverse=True)
    return {"builds": builds, "combinations": rows, "sources": [RELEASES, PYTHONS],
            "note": "Wheel availability verified; plugin and GPU driver compatibility still require validation."}


_catalog_results = {}
_catalog_lock = threading.Lock()
_build_locks = {}


def catalog(build="cpu", refresh=False):
    catalog_cache_path(build)
    settings = load_settings()
    key = (build, network_mode(settings), proxy_url(settings), settings.get('mirror_prefix') if network_mode(settings) == 'mirror' else '')
    # Share startup work with page requests instead of downloading it twice.
    with _catalog_lock:
        guard = _build_locks.setdefault(key, threading.Lock())
    with guard:
        cached = _catalog_results.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        try:
            result = _collect_catalog(build, refresh)
        except Exception as exc:
            with _catalog_lock:
                previous = cached or max((value for old_key, value in _catalog_results.items() if old_key[0] == build), default=None, key=lambda value: value[0])
            if previous:
                return {**previous[1], "refresh_error": str(exc), "stale": True}
            saved = read_catalog_cache(build)
            if saved:
                return {**saved, 'refresh_error': '在线刷新失败，正在使用最近成功缓存的版本清单：' + str(exc), 'stale': True}
            raise
        save_catalog_cache(build, result)
        with _catalog_lock:
            _catalog_results[key] = (time.monotonic(), result)
        return result


def warm_catalog():
    """Runs in a daemon worker, never on the server startup event loop."""
    try:
        first = catalog("cpu")
    except Exception:
        return
    # Bound traffic while preparing every offered CUDA channel.
    for build in first["builds"]:
        if build != "cpu":
            try:
                catalog(build)
            except Exception:
                pass


def resolve_config(value):
    if not isinstance(value, dict) or not all(value.get(key) for key in ("python", "torch", "build")):
        raise ValueError("Select Python, PyTorch and CPU/CUDA before creating an instance")
    for row in catalog(str(value["build"]))["combinations"]:
        if all(row[key] == value[key] for key in ("python", "torch", "build")):
            return row
    raise ValueError("This Python/PyTorch/build combination has no matching stable packages for this platform")
