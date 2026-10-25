import importlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest

package=types.ModuleType('_service_tests');package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')];sys.modules[package.__name__]=package
service=importlib.import_module(package.__name__+'.recovery_service')

FIXTURE="""
import argparse
from aiohttp import web
p=argparse.ArgumentParser();p.add_argument('--port',type=int);args,_=p.parse_known_args()
app=web.Application();history={}
async def stats(request):return web.json_response({'system':{}})
async def nodes(request):return web.json_response({'TestOutput':{'output_node':True}})
async def prompt(request):
    import uuid
    data=await request.json();pid=str(uuid.uuid4())
    mode=data['prompt']['1']['inputs'].get('mode','ok')
    if mode=='slow':
        import asyncio
        from pathlib import Path
        marker=Path('submissions.txt')
        marker.write_text(marker.read_text()+'1' if marker.exists() else '1')
        await asyncio.sleep(2.5)
        mode='ok'
    if mode=='reject':return web.json_response({'error':'bad input'},status=400)
    if mode!='timeout':
        history[pid]={'status':{'status_str':'success' if mode=='ok' else 'error','completed':mode=='ok','messages':[]}}
    return web.json_response({'prompt_id':pid,'node_errors':{}})
async def get_history(request):
    pid=request.match_info['pid'];return web.json_response({pid:history[pid]} if pid in history else {})
app.router.add_get('/system_stats',stats);app.router.add_get('/object_info',nodes)
app.router.add_post('/prompt',prompt);app.router.add_get('/history/{pid}',get_history)
web.run_app(app,host='127.0.0.1',port=args.port)
"""

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.core=self.root/'ComfyUI';(self.core/'comfy').mkdir(parents=True)
        (self.core/'main.py').write_text(FIXTURE)
        (self.core/'comfy/cli_args.py').write_text('--listen --port --disable-auto-launch --output-directory --temp-directory --disable-all-custom-nodes')
    def run_service(self,mode=None,expected=None,**options):
        workflow=None if mode is None else {'1':{'class_type':'TestOutput','inputs':{'mode':mode}}}
        result=service.verify_service(sys.executable,self.core,self.root/'logs',expected_nodes=expected,workflow=workflow,startup_timeout=10,workflow_timeout=.3,**options)
        self.assertTrue(result['process_stopped'])
        with socket.socket() as sock:self.assertNotEqual(sock.connect_ex(('127.0.0.1',result['port'])),0)
        return result
    def test_real_http_nodes_and_workflow(self):
        result=self.run_service('ok',['TestOutput'])
        self.assertTrue(result['http_verified']);self.assertTrue(result['nodes_verified']);self.assertTrue(result['workflow_verified'])
        self.assertEqual(len(result['workflow_sha256']),64)
    def test_missing_node_prevents_submission(self):
        result=self.run_service('ok',['Missing'])
        self.assertFalse(result['workflow_verified']);self.assertNotIn('prompt_id',result)
        self.assertEqual(result['checks'][-1]['name'],'nodes')
    def test_delayed_submission_succeeds(self):
        result=self.run_service('slow')
        self.assertTrue(result['workflow_verified'],result['checks'])
        self.assertEqual((self.core/'submissions.txt').read_text(),'1')
    def test_submission_timeout_is_not_retried(self):
        result=self.run_service('slow',submission_timeout=.2)
        self.assertFalse(result['workflow_verified'])
        self.assertNotIn('prompt_id',result)
        self.assertIn('工作流提交未确认',result['checks'][-1]['detail'])
        self.assertEqual((self.core/'submissions.txt').read_text(),'1')
    def test_workflow_error_not_success(self):
        result=self.run_service('error')
        self.assertFalse(result['workflow_verified']);self.assertEqual(result['checks'][-1]['status'],'failed')
    def test_timeout_stops_service(self):
        result=self.run_service('timeout')
        self.assertFalse(result['workflow_verified']);self.assertIn('超时',result['checks'][-1]['detail'])
    def test_no_workflow_not_claimed(self):
        result=self.run_service()
        self.assertTrue(result['http_verified']);self.assertFalse(result['nodes_verified']);self.assertFalse(result['workflow_verified'])
    def test_reject_invalid_options(self):
        with self.assertRaises(ValueError):service.validate_options(True,False,[],None,False)
        with self.assertRaises(ValueError):service.validate_options(True,True,[],{'nodes':[]},True)
        with self.assertRaises(ValueError):service.validate_options(True,True,[],{'1':{'class_type':'TestOutput','inputs':{}}},False)
        service.validate_options(True,True,['TestOutput'],{'1':{'class_type':'TestOutput','inputs':{}}},True)

if __name__=='__main__':unittest.main()
