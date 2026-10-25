import importlib
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from aiohttp import web

package=types.ModuleType('_history_route_tests')
package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')]
sys.modules[package.__name__]=package
routes_module=importlib.import_module(package.__name__+'.routes.records')
records=importlib.import_module(package.__name__+'.version_records')
compare=importlib.import_module(package.__name__+'.record_compare')


class HistoryRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes=web.RouteTableDef();routes_module.register(routes)
        self.handlers={(r.method,r.path):r.handler for r in routes}
        self.payload={'record_id':'a'*32,'other_record_id':'b'*32}
        async def body():return self.payload
        self.request=types.SimpleNamespace(remote='127.0.0.1',host='localhost:8188',headers={'X-QBB-NodeOps':'1'},content_type='application/json',match_info={'action':'compare'},json=body)

    async def test_history_and_current_comparison_routing(self):
        with patch.object(compare,'compare',return_value={'change_count':0}) as call:
            response=await self.handlers['POST','/qbb_nodeops/rebuild/{action}'](self.request)
            self.assertEqual(response.status,200);call.assert_called_once_with('a'*32,'b'*32)
            self.payload.pop('other_record_id');call.reset_mock()
            await self.handlers['POST','/qbb_nodeops/rebuild/{action}'](self.request)
            call.assert_called_once_with('a'*32,None)

    async def test_creation_forwards_checkpoint_without_executing_operation(self):
        checkpoint={'operation':'update','phase':'before','before_record_id':None}
        self.payload={'label':'Before','preview_id':'token','checkpoint':checkpoint}
        with patch.object(records,'create',return_value={'id':'a'*32}) as call:
            response=await self.handlers['POST','/qbb_nodeops/version-records'](self.request)
        self.assertEqual(response.status,200)
        call.assert_called_once_with('Before',None,'token',checkpoint=checkpoint)

    async def test_remote_request_rejected_without_comparison(self):
        self.request.remote='192.0.2.1'
        with patch.object(compare,'compare') as call:
            response=await self.handlers['POST','/qbb_nodeops/rebuild/{action}'](self.request)
        self.assertEqual(response.status,403);call.assert_not_called()

    async def test_missing_record_has_actionable_error(self):
        with patch.object(compare,'compare',side_effect=ValueError('Record not found')):
            response=await self.handlers['POST','/qbb_nodeops/rebuild/{action}'](self.request)
        self.assertEqual(response.status,400);self.assertEqual(json.loads(response.text)['error'],'Record not found')
