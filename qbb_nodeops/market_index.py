"""Persistent FTS5 index. Reads documentation only; never imports plugin code."""
import json
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError
from .market_search import normalize, CONCEPTS
from .settings import load_settings, proxy_url

DB_PATH = Path(__file__).resolve().parent.parent / 'qbb_nodeops_market.sqlite3'
_guard = threading.Lock()
_cancel = threading.Event()
_metadata_lock = threading.Lock()
_background_lock = threading.Lock()
_background_retry = 0
_status = {'running': False, 'done': 0, 'total': 0, 'failed': 0}


def tokens(text):
    text = normalize(text)
    words = re.findall(r'[a-z0-9]+', text)
    for segment in re.findall(r'[\u4e00-\u9fff]+', text):
        words.extend(segment[i:i+2] for i in range(len(segment)-1))
    return ' '.join(words)


@contextmanager
def connect():
    db = sqlite3.connect(str(DB_PATH), timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS docs(url TEXT PRIMARY KEY, text TEXT, fetched REAL, error TEXT)')
    db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(url UNINDEXED, title, body)')
    db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS repos(url TEXT PRIMARY KEY, stars INTEGER, pushed TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS repo_fetch(url TEXT PRIMARY KEY, fetched REAL, error TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS catalogue(url TEXT PRIMARY KEY, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS repo_snapshots(url TEXT PRIMARY KEY, fetched REAL NOT NULL)')
    try:
        with db:
            yield db
    finally:
        db.close()


def seed(rows):
    """Refresh changed catalogue entries; preserve downloaded documentation."""
    import hashlib
    signature = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    with connect() as db:
        old = db.execute("SELECT value FROM meta WHERE key='catalogue'").fetchone()
        if old and old[0] == signature:
            return
        docs = dict(db.execute('SELECT url,text FROM docs'))
        db.execute('DELETE FROM search')
        db.executemany('INSERT INTO search(url,title,body) VALUES(?,?,?)',
                       [(row['url'], tokens(row['title']), tokens(row['search_text'] + ' ' + docs.get(row['url'], ''))) for row in rows])
        db.execute("INSERT OR REPLACE INTO meta VALUES('catalogue',?)", (signature,))


def lookup(query, intents, extra):
    terms = set(tokens(query + ' ' + ' '.join(extra)).split())
    for tag in intents:
        terms.update(tokens(' '.join(CONCEPTS[tag])).split())
    terms -= {'the', 'and', 'to', 'for', 'with', 'want', 'help', 'please', '我想', '帮我', '一个', '可以', '插件', '需要'}
    if not terms:
        return {}
    parts = []
    for word in sorted(terms)[:100]:
        if re.fullmatch(r'[a-z0-9]{3,}', word):
            parts.append(word + '*')
        else:
            parts.append('"' + word.replace('"', '') + '"')
    expression = ' OR '.join(parts)
    with connect() as db:
        return {url: -score for url, score in db.execute('SELECT url,bm25(search,0,5,1) FROM search WHERE search MATCH ? ORDER BY bm25(search,0,5,1) LIMIT 1000', (expression,))}


def documents():
    with connect() as db:
        return dict(db.execute('SELECT url,text FROM docs WHERE text != ? ', ('',)))

def repo_metadata():
    with connect() as db:
        result = {url: {'stars':stars,'updated_at':pushed} for url,stars,pushed in db.execute('SELECT * FROM repos')}
        for url, fetched, error in db.execute('SELECT * FROM repo_fetch'):
            result.setdefault(url, {}).update(metadata_fetched=fetched, metadata_error=error)
        for url, fetched in db.execute('SELECT * FROM repo_snapshots'):
            row = result.setdefault(url, {})
            row['metadata_source'] = 'snapshot'
            if fetched >= row.get('metadata_fetched', 0):
                row.update(metadata_fetched=fetched, metadata_error='')
        return result


def import_repo_snapshot(data, urls, observed_at):
    """Use Manager's bulk statistics as cached evidence; preserve direct API results."""
    if not isinstance(data, dict):
        raise ValueError('Invalid repository statistics snapshot')
    wanted = {url.casefold(): url for url in urls}
    rows = []
    for raw, item in data.items():
        url = wanted.get(raw.rstrip('/').removesuffix('.git').casefold())
        if not url or not isinstance(item, dict) or type(item.get('stars')) is not int or item['stars'] < 0:
            continue
        try:
            stamp = datetime.fromisoformat(item['last_update'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            pushed = stamp.astimezone(timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        rows.append((url, item['stars'], pushed))
    changed = 0
    with connect() as db:
        previous = {url: (stars, pushed) for url, stars, pushed in db.execute('SELECT * FROM repos')}
        snapshots = dict(db.execute('SELECT * FROM repo_snapshots'))
        for url, stars, pushed in rows:
            if url in previous and url not in snapshots:
                continue
            if snapshots.get(url, -1) >= observed_at:
                continue
            if previous.get(url) != (stars, pushed):
                db.execute('INSERT OR REPLACE INTO repos VALUES(?,?,?)', (url, stars, pushed))
                changed += 1
            db.execute('INSERT OR REPLACE INTO repo_snapshots VALUES(?,?)', (url, observed_at))
        if changed:
            db.execute("INSERT INTO meta VALUES('market_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
    return changed


def saved_catalogue():
    with connect() as db:
        rows = [json.loads(value) for value, in db.execute('SELECT payload FROM catalogue ORDER BY rowid')]
        fetched = db.execute("SELECT value FROM meta WHERE key='catalogue_fetched'").fetchone()
    return rows, float(fetched[0]) if fetched else 0


def cache_revision():
    with connect() as db:
        row = db.execute("SELECT value FROM meta WHERE key='market_revision'").fetchone()
    return int(row[0]) if row else 0


def save_catalogue(rows, fetched):
    with connect() as db:
        old = dict(db.execute('SELECT url,payload FROM catalogue'))
        values = {row['url']: json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows}
        db.executemany('INSERT OR REPLACE INTO catalogue VALUES(?,?)',
                       [(url, value) for url, value in values.items() if old.get(url) != value])
        db.executemany('DELETE FROM catalogue WHERE url=?', [(url,) for url in old.keys() - values.keys()])
        if old != values:
            db.execute("INSERT INTO meta VALUES('market_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
        db.execute("INSERT OR REPLACE INTO meta VALUES('catalogue_fetched',?)", (str(fetched),))
    seed(rows)


def start_metadata_refresh(urls, force=False):
    """Refresh the persistent table independently of browser visibility or sorting."""
    global _background_retry
    if not urls or (not force and time.time() < _background_retry) or not _background_lock.acquire(blocking=False):
        return
    def run():
        global _background_retry
        try:
            cached = repo_metadata()
            pending = [url for url in dict.fromkeys(urls)
                       if time.time() - cached.get(url, {}).get('metadata_fetched', 0)
                       >= (300 if cached.get(url, {}).get('metadata_error') else 86400)]
            pending.sort(key=lambda url: cached.get(url, {}).get('metadata_fetched', 0))
            for offset in range(0, len(pending), 4):
                batch = [url for url in pending[offset:offset+4]
                         if time.time() - cached.get(url, {}).get('metadata_fetched', 0)
                         >= (300 if cached.get(url, {}).get('metadata_error') else 86400)]
                if not batch:
                    continue
                result = refresh_metadata(batch)
                cached = result['items']
                network_failures = sum(result['items'].get(url, {}).get('metadata_error') == 'Network or invalid response' for url in batch)
                if result['retry_after'] or network_failures == len(batch):
                    _background_retry = max(time.time() + 300, result['retry_after'])
                    break
            else:
                latest = repo_metadata()
                _background_retry = min((latest[url].get('metadata_fetched', 0) +
                    (300 if latest[url].get('metadata_error') else 86400)
                    for url in urls if url in latest), default=time.time() + 86400)
        except Exception:
            _background_retry = time.time() + 300
        finally:
            if _background_retry > time.time():
                timer = threading.Timer(_background_retry - time.time() + 1, start_metadata_refresh, args=(urls,))
                timer.daemon = True
                timer.start()
            _background_lock.release()
    try:
        threading.Thread(target=run, daemon=True, name='QBB-market-metadata').start()
    except Exception:
        _background_lock.release()
        raise

def refresh_metadata(urls):
    with _metadata_lock:
        return _refresh_metadata(urls)

def _fetch_repo_metadata(url, px):
    opener = build_opener(ProxyHandler({'https': px, 'http': px} if px else {}))
    address = 'https://api.github.com/repos/' + url.removeprefix('https://github.com/').rstrip('/').removesuffix('.git')
    try:
        with opener.open(Request(address, headers={'User-Agent': 'QBB-NodeOps'}), timeout=8) as response:
            data = json.loads(response.read(1024*1024))
        return url, (int(data['stargazers_count']), data['pushed_at']), '', 0
    except HTTPError as exc:
        retry = 0
        if exc.code in (403, 429):
            try:
                retry = max(time.time()+300, float(exc.headers.get('X-RateLimit-Reset', 0)),
                            time.time()+float(exc.headers.get('Retry-After', 0)))
            except (ValueError, TypeError):
                retry = time.time()+300
        return url, None, 'HTTP '+str(exc.code), retry
    except Exception:
        return url, None, 'Network or invalid response', 0


def _refresh_metadata(urls):
    px = proxy_url(load_settings())
    errors, retry = 0, 0
    cached = repo_metadata()
    with connect() as db:
        cooldown = db.execute("SELECT value FROM meta WHERE key='github_metadata_retry'").fetchone()
    if cooldown and float(cooldown[0]) > time.time():
        return {'ok': True, 'errors': 0, 'items': cached, 'retry_after': float(cooldown[0])}
    pending = []
    for url in list(dict.fromkeys(urls))[:30]:
        old = cached.get(url, {})
        if time.time()-old.get('metadata_fetched', 0) < (300 if old.get('metadata_error') else 86400):
            continue
        if re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?', url):
            pending.append(url)
    # Bound concurrency and stop scheduling new groups immediately after throttling.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for offset in range(0, len(pending), 4):
            results = list(pool.map(lambda url: _fetch_repo_metadata(url, px), pending[offset:offset+4]))
            with connect() as db:
                for url, data, error, wait_until in results:
                    if data is not None:
                        db.execute('INSERT INTO repos VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET stars=excluded.stars,pushed=excluded.pushed WHERE repos.stars IS NOT excluded.stars OR repos.pushed IS NOT excluded.pushed', (url, *data))
                        db.execute('DELETE FROM repo_snapshots WHERE url=?', (url,))
                    errors += bool(error)
                    retry = max(retry, wait_until)
                    db.execute('INSERT OR REPLACE INTO repo_fetch VALUES(?,?,?)', (url, time.time(), error))
                if retry:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('github_metadata_retry',?)", (str(retry),))
                if results:
                    db.execute("INSERT INTO meta VALUES('market_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
            if retry:
                break
    return {'ok': True, 'errors': errors, 'items': repo_metadata(), 'retry_after': retry}


def status():
    with connect() as db:
        count = db.execute("SELECT count(*) FROM docs WHERE text != ''").fetchone()[0]
    with _guard:
        return {**_status, 'indexed_readmes': count}


def fetch_readme(url):
    # url originates in validated catalogue, not a user supplied fetch target.
    owner_repo = url.removeprefix('https://github.com/')
    settings = load_settings()
    px = proxy_url(settings)
    opener = build_opener(ProxyHandler({'https': px, 'http': px} if px else {}))
    last = None
    for name in ('README.md', 'readme.md', 'README.rst'):
        if _cancel.is_set():
            raise RuntimeError('Cancelled')
        address = f'https://raw.githubusercontent.com/{owner_repo}/HEAD/{name}'
        if settings.get('mirror_enabled') and not px:
            address = settings['mirror_prefix'] + address
        try:
            with opener.open(Request(address, headers={'User-Agent': 'QBB-NodeOps'}), timeout=12) as response:
                text = response.read(256 * 1024).decode('utf-8', errors='replace')
                # Exclude embedded HTML, badges and URLs from search text.
                text = re.sub(r'<[^>]+>|https?://\S+', ' ', text)
                return text
        except Exception as exc:
            last = exc
            if getattr(exc, 'code', None) != 404:
                break
    raise RuntimeError(str(last))


def start(rows):
    with _guard:
        if _status['running']:
            return dict(_status)
        _cancel.clear()
        _status.update(running=True, done=0, total=len(rows), failed=0)

    def worker(row):
        if _cancel.is_set():
            return
        try:
            with connect() as db:
                old = db.execute('SELECT fetched FROM docs WHERE url=?', (row['url'],)).fetchone()
            if old and time.time() - old[0] < 7 * 86400:
                return
            text = fetch_readme(row['url'])
            with connect() as db:
                db.execute('INSERT OR REPLACE INTO docs VALUES(?,?,?,?)', (row['url'], text, time.time(), ''))
                db.execute('DELETE FROM search WHERE url=?', (row['url'],))
                db.execute('INSERT INTO search(url,title,body) VALUES(?,?,?)', (row['url'], tokens(row['title']), tokens(row['search_text'] + ' ' + text)))
        except Exception:
            with _guard:
                _status['failed'] += 1
        finally:
            with _guard:
                _status['done'] += 1

    def run():
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(worker, rows))
        finally:
            with _guard:
                _status['running'] = False
    threading.Thread(target=run, daemon=True, name='QBB-market-index').start()
    return status()


def stop():
    _cancel.set()
    return status()
