"""Explicit, task-local networking for dependency installation."""
import os
import socket
from urllib.parse import urlsplit

from .settings import load_settings, proxy_url


def network_environment(settings=None):
    settings = load_settings() if settings is None else settings
    proxy = proxy_url(settings) or ''
    if settings.get('proxy_enabled') and not proxy:
        raise RuntimeError('插件代理配置无效，请检查网络设置。')
    env = os.environ.copy()
    for key in list(env):
        if key.lower() in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy') or key.upper().startswith('PIP_'):
            env.pop(key, None)
    # Do not inherit stale pip.ini proxies/indexes from the target environment.
    env['PIP_CONFIG_FILE'] = os.devnull
    if proxy:
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy)
    else:
        env['NO_PROXY'] = '*'
    return env, proxy


def pip_network():
    env, proxy = network_environment()
    args = ['--index-url', 'https://pypi.org/simple', '--retries', '2', '--timeout', '30']
    if proxy:
        address = urlsplit(proxy)
        try:
            with socket.create_connection((address.hostname, address.port), timeout=5):
                pass
        except OSError as exc:
            raise RuntimeError(f'无法连接插件配置的代理 {proxy}。请启动代理软件并确认端口；尚未开始安装依赖。') from exc
        args.extend(['--proxy', proxy])
    return env, args, proxy or '直连 PyPI（GitHub 镜像不用于 Python 依赖）'


def install_error(output):
    lower = output.lower()
    if 'proxyerror' in lower or 'cannot connect to proxy' in lower:
        return '代理连接失败，请检查插件网络设置与代理软件；此错误不能说明依赖版本不兼容。'
    if any(word in lower for word in ('connectionerror', 'connection refused', 'failed to establish a new connection', 'timed out', 'name resolution', 'sslerror', 'certificate_verify_failed')):
        return '访问依赖源失败，请检查网络、代理或证书；尚不能判断依赖版本是否兼容。'
    if 'no matching distribution' in lower or 'could not find a version' in lower:
        return '依赖源没有返回符合要求的安装包，请检查目标 Python、系统架构及依赖版本限制。'
    return '依赖安装失败，请查看下方详细日志。'
