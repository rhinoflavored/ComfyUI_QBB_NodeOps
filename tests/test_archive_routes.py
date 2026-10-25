import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from aiohttp import web

package=types.ModuleType('_archive_route_tests')
package.__path__=[str(Path(__file__).parents[1]/'qbb_nodeops')]
sys.modules[package.__name__]=package
routes_module=importlib.import_module(package.__name__+'.routes.records')
archive=importlib.import_module(package.__name__+'.record_archives')


class ArchiveRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        routes=web.RouteTableDef();routes_module.register(routes)
        self.handler=next(r.handler for r in routes if r.path=='/qbb_nodeops/version-records/archive/{action}')
        async def body():return {'record_id':'a'*32,'path':'C:/backup.zip','token':'preview','confirmed':True}
        self.request=types.SimpleNamespace(remote='127.0.0.1',host='localhost:8188',headers={'X-QBB-NodeOps':'1'},content_type='application/json',match_info={},json=body)

    async def test_new_readonly_actions_route_without_mutations(self):
        for action,function,args in [('export-preview','preview_export',('a'*32,'C:/backup.zip')),('remove-preview','preview_remove',('a'*32,)),('check','check_record',('a'*32,))]:
            with self.subTest(action=action),patch.object(archive,function,return_value={}) as call:
                self.request.match_info['action']=action
                self.assertEqual((await self.handler(self.request)).status,200)
                call.assert_called_once_with(*args)

    async def test_confirmed_actions_forward_preview_token(self):
        for action,function,args in [('export','export_bundle',('a'*32,'C:/backup.zip',True)),('remove','remove_record',('a'*32,True))]:
            with self.subTest(action=action),patch.object(archive,function,return_value={}) as call:
                self.request.match_info['action']=action
                self.assertEqual((await self.handler(self.request)).status,200)
                call.assert_called_once_with(*args,token='preview')

    async def test_remote_audit_rejected_before_reading(self):
        self.request.remote='192.0.2.1';self.request.match_info['action']='check'
        with patch.object(archive,'check_record') as call:
            self.assertEqual((await self.handler(self.request)).status,403);call.assert_not_called()
