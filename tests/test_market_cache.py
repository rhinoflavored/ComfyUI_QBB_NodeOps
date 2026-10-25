import importlib
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch, MagicMock

root = Path(__file__).parents[1]
package = types.ModuleType('_market_cache_tests')
package.__path__ = [str(root/'qbb_nodeops')]
sys.modules[package.__name__] = package
market = importlib.import_module(package.__name__+'.market')
index = market.market_index

def catalogue(title='Saved plugin'):
    return {'custom_nodes':[{'title':title,'install_type':'git-clone','files':['https://github.com/example/plugin'],'description':'Image plugin'}]}

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        timer = patch.object(threading, 'Timer');timer.start();self.addCleanup(timer.stop)
        for target, name, value in [(index,'DB_PATH',self.directory/'market.sqlite3'),(market,'_catalogue',[]),
                (market,'_updated',0),(market,'_retry_at',0),(index,'_background_retry',0),
                (market,'_snapshot_seeded',False),
                (market,'_custom_nodes_dirs',lambda:[]),(market,'load_settings',lambda:{}),
                (index,'load_settings',lambda:{}),(market,'proxy_url',lambda settings:None),
                (index,'proxy_url',lambda settings:None)]:
            mock = patch.object(target,name,value);mock.start();self.addCleanup(mock.stop)

    def save(self):
        rows = market._catalogue_rows(catalogue(),None)
        index.save_catalogue(rows,1)
        with index.connect() as db:
            db.execute('INSERT INTO repos VALUES(?,?,?)',(rows[0]['url'],123,'2026-10-01'))
        return rows

    def finish_refresh(self):
        self.assertTrue(market._refresh_lock.acquire(timeout=3))
        market._refresh_lock.release()

    def test_restart_reads_saved_rows_without_waiting_for_network(self):
        self.save()
        entered, release = threading.Event(), threading.Event()
        def open_url(*args,**kwargs):
            entered.set()
            release.wait(3)
            raise OSError('offline')
        opener=MagicMock();opener.open.side_effect=open_url
        with patch.object(market,'build_opener',return_value=opener),patch.object(index,'start_metadata_refresh'):
            try:
                result=market.search()
                self.assertTrue(entered.wait(1))
                self.assertTrue(market._refresh_lock.locked())
                self.assertEqual(result['items'][0]['title'],'Saved plugin')
                self.assertEqual(result['items'][0]['stars'],123)
                self.assertEqual(result['items'][0]['updated_at'],'2026-10-01')
            finally:
                release.set();self.finish_refresh()
        self.assertEqual(index.saved_catalogue()[0][0]['title'],'Saved plugin')

    def test_background_success_updates_table_and_survives_memory_reset(self):
        self.save()
        opener=MagicMock();opener.open.return_value.__enter__.return_value.read.return_value=json.dumps(catalogue('Updated plugin')).encode()
        with patch.object(market,'build_opener',return_value=opener),patch.object(index,'start_metadata_refresh'):
            market.search(refresh=True);self.finish_refresh()
            market._catalogue=[]
            result=market.search()
        self.assertEqual(result['items'][0]['title'],'Updated plugin')
        self.assertEqual(result['items'][0]['stars'],123)

    def test_cache_save_changes_only_changed_rows_and_retains_metadata(self):
        rows=self.save()
        with index.connect() as db:
            db.execute('CREATE TABLE audit(url TEXT)')
            db.execute('CREATE TRIGGER changed AFTER INSERT ON catalogue BEGIN INSERT INTO audit VALUES(new.url); END')
        index.save_catalogue(rows,2)
        revision=index.cache_revision()
        index.save_catalogue(rows,2)
        self.assertEqual(index.cache_revision(),revision)
        with index.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],0)
        index.save_catalogue([{**rows[0],'title':'Changed'}],3)
        self.assertGreater(index.cache_revision(),revision)
        with index.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],1)
        self.assertEqual(index.repo_metadata()[rows[0]['url']]['stars'],123)

    def test_local_manager_bootstraps_table_once(self):
        manager=self.directory/'ComfyUI-Manager';manager.mkdir()
        source=manager/'custom-node-list.json';source.write_text(json.dumps(catalogue()))
        with patch.object(market,'_custom_nodes_dirs',return_value=[str(self.directory)]),patch.object(market,'refresh_catalogue'),patch.object(index,'start_metadata_refresh'):
            self.assertEqual(len(market.search()['items']),1)
            source.unlink();market._catalogue=[]
            self.assertEqual(len(market.search()['items']),1)

    def test_metadata_refresh_runs_all_rows_without_browser_and_skips_fresh_rows(self):
        urls=['https://github.com/example/repo'+str(i) for i in range(9)]
        with index.connect() as db:db.execute('INSERT INTO repo_fetch VALUES(?,?,?)',(urls[0],time.time(),''))
        calls=[]
        def fetch(url,proxy):
            calls.append(url);return url,(10,'2026-10-01'),'',0
        with patch.object(index,'_fetch_repo_metadata',side_effect=fetch):
            index.start_metadata_refresh(urls)
            self.assertTrue(index._background_lock.acquire(timeout=3));index._background_lock.release()
        self.assertEqual(set(calls),set(urls[1:]))
        self.assertEqual(len(index.repo_metadata()),9)

    def test_metadata_rate_limit_preserves_cache_and_schedules_retry(self):
        rows=self.save();url=rows[0]['url']
        with patch.object(index,'_fetch_repo_metadata',return_value=(url,None,'HTTP 429',time.time()+600)),patch.object(index.threading,'Timer') as timer:
            index.start_metadata_refresh([url])
            self.assertTrue(index._background_lock.acquire(timeout=3));index._background_lock.release()
            self.assertTrue(timer.called)
            self.assertTrue(timer.return_value.daemon)
        self.assertEqual(index.repo_metadata()[url]['stars'],123)

    def test_one_network_failure_does_not_pause_other_repositories(self):
        urls=['https://github.com/example/repo'+str(i) for i in range(9)]
        calls=[]
        def fetch(url,proxy):
            calls.append(url)
            return (url,None,'Network or invalid response',0) if url==urls[0] else (url,(10,'2026-10-01'),'',0)
        with patch.object(index,'_fetch_repo_metadata',side_effect=fetch):
            index.start_metadata_refresh(urls)
            self.assertTrue(index._background_lock.acquire(timeout=3));index._background_lock.release()
        self.assertEqual(set(calls),set(urls))
        self.assertEqual(sum('updated_at' in row for row in index.repo_metadata().values()),8)

    def test_whole_batch_network_failure_still_pauses_queue(self):
        urls=['https://github.com/example/repo'+str(i) for i in range(9)]
        def fetch(url,proxy):return url,None,'Network or invalid response',0
        with patch.object(index,'_fetch_repo_metadata',side_effect=fetch) as fetcher:
            index.start_metadata_refresh(urls)
            self.assertTrue(index._background_lock.acquire(timeout=3));index._background_lock.release()
        self.assertEqual(fetcher.call_count,4)
        self.assertGreater(index._background_retry,time.time())

    def test_snapshot_arriving_mid_queue_skips_redundant_api_requests(self):
        urls=['https://github.com/example/repo'+str(i) for i in range(12)]
        calls=[]
        def fetch(url,proxy):
            calls.append(url)
            if url==urls[0]:
                index.import_repo_snapshot({u:{'stars':20,'last_update':'2026-10-01'} for u in urls[4:]},urls,time.time())
            return url,(10,'2026-10-01'),'',0
        with patch.object(index,'_fetch_repo_metadata',side_effect=fetch):
            index.start_metadata_refresh(urls)
            self.assertTrue(index._background_lock.acquire(timeout=3));index._background_lock.release()
        self.assertEqual(set(calls),set(urls[:4]))

    def test_bulk_snapshot_preserves_direct_results_and_validates_entries(self):
        old=self.save()[0]['url'];new='https://github.com/example/second'
        data={old:{'stars':1,'last_update':'2026-10-01 03:00:00'},
              new:{'stars':20,'last_update':'2026-10-02 04:00:00'},
              'https://github.com/unlisted/repo':{'stars':30,'last_update':'2026-10-01'},
              'https://github.com/example/bad':{'stars':30,'last_update':'invalid'}}
        self.assertEqual(index.import_repo_snapshot(data,[old,new,'https://github.com/example/bad'],10),1)
        saved=index.repo_metadata()
        self.assertEqual(saved[old]['stars'],123)
        self.assertEqual(saved[new]['stars'],20)
        self.assertEqual(saved[new]['metadata_source'],'snapshot')
        self.assertEqual(saved[new]['updated_at'],'2026-10-02T04:00:00+00:00')
        self.assertEqual(len(saved),2)
        self.assertEqual(index.import_repo_snapshot({new:{'stars':1,'last_update':'2025-01-01'}},[new],5),0)
        self.assertEqual(index.repo_metadata()[new]['stars'],20)

    def test_fresh_snapshot_avoids_api_calls_and_later_api_success_replaces_it(self):
        url='https://github.com/example/plugin';now=time.time()
        index.import_repo_snapshot({url:{'stars':20,'last_update':'2026-10-01'}},[url],now)
        with patch.object(index,'_fetch_repo_metadata',side_effect=AssertionError('Fresh snapshot does not need API calls')):
            index.start_metadata_refresh([url])
            self.assertTrue(index._background_lock.acquire(timeout=3));index._background_lock.release()
        with patch.object(index.time,'time',return_value=now+90000),patch.object(index,'_fetch_repo_metadata',return_value=(url,(21,'2026-10-03T00:00:00Z'),'',0)):
            index.refresh_metadata([url])
        saved=index.repo_metadata()[url]
        self.assertEqual(saved['stars'],21)
        self.assertNotIn('metadata_source',saved)

    def test_local_statistics_fill_cache_before_first_search_returns(self):
        manager=self.directory/'ComfyUI-Manager';manager.mkdir()
        (manager/'custom-node-list.json').write_text(json.dumps(catalogue()))
        (manager/'github-stats.json').write_text(json.dumps({'https://github.com/example/plugin':{'stars':40,'last_update':'2026-10-01'}}))
        with patch.object(market,'_custom_nodes_dirs',return_value=[str(self.directory)]),patch.object(market,'refresh_catalogue'),patch.object(index,'start_metadata_refresh'):
            result=market.search()
        self.assertEqual(result['items'][0]['stars'],40)
        self.assertEqual(result['items'][0]['metadata_source'],'snapshot')

    def test_cache_status_is_local_only_and_reads_persisted_revision(self):
        from aiohttp import web
        routes = importlib.import_module(package.__name__+'.routes.market')
        table=web.RouteTableDef();routes.register(table)
        handler=next(row.handler for row in table if row.path=='/qbb_nodeops/market/cache-status')
        request=types.SimpleNamespace(remote='127.0.0.1',host='localhost:8188',headers={'X-QBB-NodeOps':'1'})
        self.save()
        response=asyncio.run(handler(request))
        self.assertEqual(response.status,200)
        self.assertEqual(json.loads(response.text)['revision'],index.cache_revision())
        request.remote='192.0.2.1'
        with patch.object(index,'cache_revision') as read:
            self.assertEqual(asyncio.run(handler(request)).status,403)
            read.assert_not_called()

if __name__=='__main__':unittest.main()
