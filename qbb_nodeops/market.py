"""Searchable Manager catalogue and bounded GitHub repository downloads."""
import json
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from .settings import load_settings, proxy_url
from .git_tools import require_git
from .update_check import _custom_nodes_dirs, _run_git, _apply_mirror
from .market_search import CONCEPTS, TAG_GROUPS, evidence, interpret, normalize, rank
from . import market_index

SOURCE = 'https://raw.githubusercontent.com/Comfy-Org/ComfyUI-Manager/main/custom-node-list.json'
_catalogue = []
_updated = 0
_lock = threading.Lock()
_install_lock = threading.Lock()


def repository_url(value):
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?', str(value).strip())
    if not match or any(p in ('.', '..') for p in match.groups()):
        raise ValueError('Please use https://github.com/owner/repository')
    return 'https://github.com/' + '/'.join(match.groups())


def classify(item):
    return list(evidence(item))


def _local_catalogue():
    return next((Path(root) / 'ComfyUI-Manager' / 'custom-node-list.json'
                 for root in _custom_nodes_dirs()
                 if (Path(root) / 'ComfyUI-Manager' / 'custom-node-list.json').is_file()), None)


def _catalogue_rows(data, local):
    rows = []
    seen = set()
    node_map = {}
    if local:
        mapping = local.parent / 'extension-node-map.json'
        if mapping.is_file():
            try:
                node_map = json.loads(mapping.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                pass
    for item in data['custom_nodes']:
        if item.get('install_type') != 'git-clone':
            continue
        try:
            repo = repository_url((item.get('files') or [''])[0])
        except ValueError:
            continue
        if repo.casefold() in seen:
            continue
        seen.add(repo.casefold())
        mapped = node_map.get(repo, node_map.get(repo + '.git', []))
        if isinstance(mapped, list) and mapped and isinstance(mapped[0], list):
            item = {**item, 'preemptions': list(item.get('preemptions') or []) + mapped[0]}
        proof = evidence(item)
        rows.append(dict(title=item.get('title') or repo.split('/')[-1], description=item.get('description', ''), url=repo, tags=list(proof), evidence=proof,
                         search_text=normalize(' '.join(str(item.get(k, '')) for k in ('title', 'description', 'preemptions', 'nodename_pattern')))))
    return rows

_refresh_lock = threading.Lock()
_retry_at = 0
_refresh_timer = None
_snapshot_seeded = False


def refresh_catalogue(force=False):
    """Serve persisted rows first; never make a search wait for the network."""
    if not force and (time.time() < _retry_at or time.time() - _updated < 21600):
        return
    if not _refresh_lock.acquire(blocking=False):
        return
    def run():
        global _catalogue, _updated, _retry_at, _refresh_timer
        try:
            settings = load_settings()
            px = proxy_url(settings)
            url = settings['mirror_prefix'] + SOURCE if settings.get('mirror_enabled') and not px else SOURCE
            opener = build_opener(ProxyHandler({'http': px, 'https': px} if px else {}))
            with opener.open(url, timeout=25) as response:
                data = json.loads(response.read(16 * 1024 * 1024))
            rows = _catalogue_rows(data, _local_catalogue())
            if not rows:
                raise ValueError('Empty catalogue response')
            fetched = time.time()
            with _lock:
                market_index.save_catalogue(rows, fetched)
                _catalogue, _updated = rows, fetched
            try:
                with opener.open(url.rsplit('/', 1)[0] + '/github-stats.json', timeout=15) as response:
                    statistics = json.loads(response.read(16 * 1024 * 1024))
                market_index.import_repo_snapshot(statistics, [row['url'] for row in rows], fetched)
            except Exception:
                pass  # Catalogue updates remain usable when the optional statistics source fails.
            _retry_at = 0
        except Exception:
            _retry_at = time.time() + 300
        finally:
            with _lock:
                urls = [row['url'] for row in _catalogue]
            if _refresh_timer is not None:
                _refresh_timer.cancel()
            due = _retry_at if _retry_at > time.time() else _updated + 21600
            _refresh_timer = threading.Timer(max(1, due - time.time()), refresh_catalogue)
            _refresh_timer.daemon = True
            _refresh_timer.start()
            market_index.start_metadata_refresh(urls, force=_retry_at == 0)
            _refresh_lock.release()
    try:
        threading.Thread(target=run, daemon=True, name='QBB-market-catalogue').start()
    except Exception:
        _refresh_lock.release()
        raise


def search(query='', refresh=False):
    global _catalogue, _updated, _snapshot_seeded
    warning = ''
    with _lock:
        if not _catalogue:
            rows, fetched = market_index.saved_catalogue()
            if not rows:
                local = _local_catalogue()
                if local:
                    rows = _catalogue_rows(json.loads(local.read_text(encoding='utf-8')), local)
                    market_index.save_catalogue(rows, 0)
            _catalogue, _updated = rows, fetched
        if not _snapshot_seeded:
            local = _local_catalogue()
            statistics = local.with_name('github-stats.json') if local else None
            if statistics and statistics.is_file():
                try:
                    market_index.import_repo_snapshot(json.loads(statistics.read_text(encoding='utf-8')),
                        [row['url'] for row in _catalogue], statistics.stat().st_mtime)
                except (OSError, ValueError):
                    pass
            _snapshot_seeded = True
        catalogue = list(_catalogue)
        cache_revision = market_index.cache_revision()
    refresh_catalogue(refresh)
    market_index.start_metadata_refresh([row['url'] for row in catalogue])
    roots = [Path(p) for p in _custom_nodes_dirs()]
    query = str(query).strip()[:500]
    _, inferred, words = interpret(query)
    hits = market_index.lookup(query, inferred, words) if query else {}
    docs = market_index.documents() if hits else {}
    candidates = []
    for item in catalogue:
        if item['url'] in hits and item['url'] in docs:
            doc = docs[item['url']]
            proof = item['evidence']
            candidates.append({**item, 'evidence': proof, 'tags': list(proof), 'search_text': item['search_text'] + ' ' + normalize(doc)})
        else:
            candidates.append(item)
    result, intents, relaxed = rank(candidates, query)
    present = {item['url'] for item in result}
    for item in candidates:
        if item['url'] in hits and item['url'] not in present:
            result.append({k: v for k, v in item.items() if k not in ('evidence', 'search_text')})
    for item in result:
        item['score'] = item.get('score', 0) + min(hits.get(item['url'], 0) * 8, 80)
        doc = docs.get(item['url'], '')
        if item['url'] in hits and doc:
            needles = item.get('evidence_terms', []) + query.split()
            offset = next((doc.casefold().find(word.casefold()) for word in needles if word and word.casefold() in doc.casefold()), 0)
            item['readme_excerpt'] = doc[max(0, offset - 60):max(0, offset - 60) + 320]
            item['readme_url'] = item['url'] + '#readme'
    result.sort(key=lambda item: (-item['score'], item['title'].casefold()))
    metadata = market_index.repo_metadata()
    for row in result:
        row.update(metadata.get(row['url'], {'stars':None,'updated_at':None}))
        name = row['url'].split('/')[-1]
        row['installed'] = any((root / name).exists() for root in roots)
    return {'items': result, 'intents': intents, 'expanded_terms': [], 'index': market_index.status(), 'total': len(catalogue), 'refreshing': _refresh_lock.locked(), 'cache_revision': cache_revision, 'tags': list(CONCEPTS), 'tag_groups': TAG_GROUPS, 'warning': warning, 'relaxed': relaxed, 'source': SOURCE}


def build_index():
    if not _catalogue:
        search()
    return market_index.start(list(_catalogue))


def install(url):
    require_git()
    url = repository_url(url)
    roots = _custom_nodes_dirs()
    if not roots:
        raise RuntimeError('No custom_nodes directory found')
    root = Path(roots[0]).resolve()
    target = root / url.split('/')[-1]
    if target.resolve().parent != root:
        raise ValueError('Invalid destination')
    if not _install_lock.acquire(blocking=False):
        raise RuntimeError('Another download is running')
    try:
        if target.exists():
            raise ValueError('Destination already exists; use plugin updates')
        settings = load_settings()
        with tempfile.TemporaryDirectory(prefix='qbb-download-') as tmp:
            staging = Path(tmp) / 'repo'
            code, stdout, stderr = _run_git(['clone', '--depth', '1', '--', _apply_mirror(url, settings), str(staging)], cwd=tmp, settings=settings, timeout=180)
            if code:
                raise RuntimeError((stderr or stdout)[-2000:])
            subprocess.run(['git', '-C', str(staging), 'remote', 'set-url', 'origin', url], check=True, capture_output=True, timeout=10)
            # Copy only after a complete clone; never overwrite another plugin.
            import shutil
            shutil.copytree(staging, target)
        return {'ok': True, 'url': url, 'dependencies_required': (target / 'requirements.txt').exists() or (target / 'install.py').exists()}
    finally:
        _install_lock.release()
