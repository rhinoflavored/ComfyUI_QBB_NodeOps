"""Offline regression tests: no real proxies, downloads or installations."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

package = types.ModuleType('_network_policy_tests')
package.__path__ = [str(Path(__file__).parents[1] / 'qbb_nodeops')]
sys.modules[package.__name__] = package
catalog = importlib.import_module(package.__name__ + '.runtime_catalog')
git = importlib.import_module(package.__name__ + '.update_check')
network = importlib.import_module(package.__name__ + '.install_network')
runtime = importlib.import_module(package.__name__ + '.runtime_install')
rebuild = importlib.import_module(package.__name__ + '.rebuild')
ai = importlib.import_module(package.__name__ + '.ai_service')


class NetworkPolicyTests(unittest.TestCase):
    def test_catalog_mirror_only_routes_github_metadata(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'ok'
        settings = {'proxy_enabled': False, 'mirror_enabled': True, 'mirror_prefix': 'https://mirror.invalid/'}
        with patch.object(catalog, 'load_settings', return_value=settings), patch.object(catalog, 'build_opener', return_value=opener):
            catalog.fetch(catalog.PYTHONS, refresh=True)
            self.assertEqual(opener.open.call_args.args[0].full_url, settings['mirror_prefix'] + catalog.PYTHONS)
            catalog.fetch(catalog.RELEASES, refresh=True)
            self.assertEqual(opener.open.call_args.args[0].full_url, catalog.RELEASES)
            opener.open.side_effect = OSError('offline')
            with self.assertRaisesRegex(RuntimeError, '仅 GitHub 镜像不能代理'):
                catalog.fetch(catalog.RELEASES, refresh=True)

    def test_catalog_reloads_after_policy_change_and_preserves_last_success(self):
        direct = {'proxy_enabled': False, 'mirror_enabled': False}
        mirror = {**direct, 'mirror_enabled': True, 'mirror_prefix': 'https://mirror.invalid/'}
        result = {'builds': ['cpu'], 'combinations': [{'build': 'cpu'}]}
        with patch.object(catalog, '_catalog_results', {}), patch.object(catalog, 'load_settings', side_effect=[direct, mirror]), patch.object(catalog, '_collect_catalog', side_effect=[result, RuntimeError('mirror failed')]) as collect:
            self.assertEqual(catalog.catalog(), result)
            fallback = catalog.catalog()
            self.assertEqual(collect.call_count, 2)
            self.assertTrue(fallback['stale'])
            self.assertEqual(fallback['combinations'], result['combinations'])
            self.assertEqual(fallback['refresh_error'], 'mirror failed')

    def test_ai_opener_always_has_explicit_proxy_policy(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{}'
        for proxy, local in [('', False), ('http://localhost:18888', False), ('http://localhost:18888', True)]:
            with self.subTest(proxy=proxy, local=local), patch.object(ai, 'proxy_url', return_value=proxy), patch.object(ai.urllib.request, 'build_opener', return_value=opener) as build:
                ai._json_request('http://localhost:1234/v1/chat/completions', {}, '', local=local)
                handler = next(h for h in build.call_args.args if isinstance(h, ai.urllib.request.ProxyHandler))
                self.assertEqual(handler.proxies, {'http': proxy, 'https': proxy} if proxy and not local else {})

    def test_recovery_commands_ignore_old_network_configuration(self):
        poison = {'HTTPS_PROXY': 'http://old:9', 'NO_PROXY': '*', 'PIP_PROXY': 'old',
                  'UV_CONFIG_FILE': 'old.toml', 'UV_PYTHON_INSTALL_MIRROR': 'old', 'GIT_CONFIG_COUNT': '1'}
        for proxy in ('', 'http://localhost:18888'):
            with self.subTest(proxy=proxy), patch.dict(os.environ, poison), patch.object(rebuild, 'load_settings', return_value={'proxy_enabled': bool(proxy)}), patch.object(network, 'proxy_url', return_value=proxy), patch.object(rebuild.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='ok', stderr='')) as run:
                rebuild.run(['uv', 'python', 'install', '3.12'], extra_env={'UV_PYTHON_INSTALL_DIR': 'managed'})
                env = run.call_args.kwargs['env']
                self.assertEqual(env.get('HTTPS_PROXY', ''), proxy)
                self.assertEqual(env.get('NO_PROXY'), None if proxy else '*')
                for key in ('PIP_PROXY', 'UV_CONFIG_FILE', 'UV_PYTHON_INSTALL_MIRROR', 'GIT_CONFIG_COUNT'):
                    self.assertNotIn(key, env)
                self.assertEqual(env['UV_NO_CONFIG'], '1')
                self.assertEqual(env['UV_PYTHON_INSTALL_DIR'], 'managed')
                self.assertEqual(env['PIP_CONFIG_FILE'], os.devnull)
                self.assertEqual(env['GIT_CONFIG_GLOBAL'], os.devnull)

    def test_recovery_git_uses_mirror_and_explicit_direct_proxy(self):
        settings = {'proxy_enabled': False, 'mirror_enabled': True, 'mirror_prefix': 'https://mirror.invalid/'}
        with patch.object(rebuild, 'load_settings', return_value=settings), patch.object(network, 'proxy_url', return_value=None), patch.object(rebuild, '_network_fetch_url', return_value='https://mirror.invalid/repo') as mirror, patch.object(rebuild.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='', stderr='')) as run:
            rebuild.run(['git', 'clone', '--', 'https://github.com/example/repo', 'target'])
        mirror.assert_called_once_with('https://github.com/example/repo', settings)
        command = run.call_args.args[0]
        self.assertIn('http.proxy=', command)
        self.assertIn('https://mirror.invalid/repo', command)

    def test_catalog_uses_fresh_explicit_opener(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'ok'
        with patch.object(catalog, 'load_settings', return_value={}), patch.object(catalog, 'proxy_url', side_effect=['http://localhost:7890', 'http://localhost:18888', '']), patch.object(catalog, 'build_opener', return_value=opener) as build:
            for unused in range(3):
                self.assertEqual(catalog.fetch('https://example.invalid', refresh=True), 'ok')
        self.assertEqual([c.args[0].proxies for c in build.call_args_list], [
            {'http': 'http://localhost:7890', 'https': 'http://localhost:7890'},
            {'http': 'http://localhost:18888', 'https': 'http://localhost:18888'}, {}])

    def test_git_proxy_and_direct_ignore_inherited_bypass(self):
        with patch.dict(os.environ, {'NO_PROXY': '*', 'hTtP_pRoXy': 'old'}):
            with patch.object(git, 'proxy_url', return_value='http://localhost:18888'):
                env = git._git_env({})
                self.assertNotIn('NO_PROXY', env)
                self.assertNotIn('hTtP_pRoXy', env)
                self.assertEqual(env['HTTPS_PROXY'], 'http://localhost:18888')
            with patch.object(git, 'proxy_url', return_value=''):
                env = git._git_env({})
                self.assertEqual(env['NO_PROXY'], '*')
                self.assertNotIn('hTtP_pRoXy', env)
            self.assertEqual(os.environ['hTtP_pRoXy'], 'old')

    def test_git_overrides_url_specific_proxy(self):
        url = 'https://github.com/example/repo'
        with patch.object(git, 'proxy_url', return_value=''), patch.object(git, 'network_mode', return_value='direct'), patch.object(git.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='', stderr='')) as run:
            git._run_git(['clone', '--', url, 'target'], '.', settings={'proxy_enabled': False})
        self.assertIn('http.' + url + '.proxy=', run.call_args.args[0])
        self.assertIn('remote.origin.proxy=', run.call_args.args[0])

    def test_pip_ignores_external_config(self):
        with patch.dict(os.environ, {'PIP_CONFIG_FILE': 'old.ini', 'PIP_PROXY': 'old', 'ALL_PROXY': 'old'}), patch.object(network, 'load_settings', return_value={}), patch.object(network, 'proxy_url', return_value=''):
            env, args, label = network.pip_network()
        self.assertEqual(env['PIP_CONFIG_FILE'], os.devnull)
        self.assertNotIn('PIP_PROXY', env)
        self.assertNotIn('ALL_PROXY', env)
        self.assertIn('https://pypi.org/simple', args)

    def test_runtime_all_download_steps_use_task_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            uv = root / '.runtime-tools' / 'bin' / ('uv.exe' if os.name == 'nt' else 'uv')
            uv.parent.mkdir(parents=True)
            uv.touch()
            req = root / 'requirements.txt'
            req.touch()
            python = root / '.python' / 'python.exe'
            config = {'python': '3.12', 'build': 'cpu', 'pins': {'torch': '2.0'}}
            calls = []
            def run(args, **kwargs):
                calls.append((args, kwargs['env'].copy()))
                return types.SimpleNamespace(returncode=0, stdout=str(python) if 'find' in args else '')
            with patch.object(runtime, 'validate_config', return_value=config), patch.object(runtime, 'pip_network', return_value=({'UV_CONFIG_FILE': 'old.toml', 'UV_PYTHON_INSTALL_MIRROR': 'old', 'PIP_CONFIG_FILE': os.devnull, 'HTTPS_PROXY': 'http://localhost:18888'}, ['--index-url', 'https://pypi.org/simple', '--proxy', 'http://localhost:18888'], 'test')), patch.object(runtime.subprocess, 'run', side_effect=run):
                runtime.install_runtime(root, config, [req], lambda text: None)
            for args, env in calls:
                self.assertEqual(env['HTTPS_PROXY'], 'http://localhost:18888')
                self.assertEqual(env['UV_NO_CONFIG'], '1')
                self.assertNotIn('UV_CONFIG_FILE', env)
                self.assertNotIn('UV_PYTHON_INSTALL_MIRROR', env)
                if 'pip' in args and 'install' in args:
                    self.assertIn('--proxy', args)
                    expected = 'https://download.pytorch.org/whl/cpu' if 'torch==2.0' in args else 'https://pypi.org/simple'
                    self.assertEqual(args[args.index('--index-url') + 1], expected)


if __name__ == '__main__':
    unittest.main()
