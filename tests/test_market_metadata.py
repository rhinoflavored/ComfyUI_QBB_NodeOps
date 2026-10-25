import ast
import re
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

SOURCE=Path(__file__).parents[1]/'qbb_nodeops/market_index.py'

class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        names={'connect','repo_metadata','_refresh_metadata'}
        tree=ast.parse(SOURCE.read_text(encoding='utf-8-sig'))
        self.s=dict(sqlite3=sqlite3,contextmanager=contextmanager,time=time,re=re,ThreadPoolExecutor=ThreadPoolExecutor,
                    DB_PATH=Path(self.temp.name)/'test.db',load_settings=lambda:{},proxy_url=lambda settings:None)
        exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(SOURCE),'exec'),self.s)

    def test_concurrent_and_cached(self):
        barrier=threading.Barrier(4)
        calls=[]
        def fetch(url,px):
            calls.append(url);barrier.wait(timeout=3)
            return url,(100,'2026-01-01'),' ',0
        self.s['_fetch_repo_metadata']=fetch
        urls=['https://github.com/test/repo'+str(i) for i in range(4)]
        result=self.s['_refresh_metadata'](urls)
        self.assertEqual(len(result['items']),4)
        self.s['_refresh_metadata'](urls)
        self.assertEqual(len(calls),4)

    def test_limit_stops_next_group_and_preserves_old_stars(self):
        urls=['https://github.com/test/repo'+str(i) for i in range(9)]
        with self.s['connect']() as db:
            db.execute('INSERT INTO repos VALUES(?,?,?)',(urls[0],123,'2026-01-01'))
        calls=[]
        def fetch(url,px):
            calls.append(url)
            return url,None,'HTTP 429',time.time()+600
        self.s['_fetch_repo_metadata']=fetch
        result=self.s['_refresh_metadata'](urls)
        self.assertEqual(len(calls),4)
        self.assertEqual(result['items'][urls[0]]['stars'],123)
        self.assertGreater(result['retry_after'],time.time())
        self.s['_refresh_metadata'](urls)
        self.assertEqual(len(calls),4)

if __name__=='__main__':unittest.main()
