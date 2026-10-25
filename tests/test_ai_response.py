"""Offline response validation tests; no API calls or credentials required."""
import ast
from pathlib import Path
from typing import Any
import unittest
import urllib.parse

source = Path(__file__).parents[1] / 'qbb_nodeops/ai_service.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'response_text')
namespace = {'Any': Any}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
response_text = namespace['response_text']
payload_function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'chat_payload')
namespace['urllib'] = urllib
exec(compile(ast.Module(body=[payload_function], type_ignores=[]), str(source), 'exec'), namespace)
chat_payload = namespace['chat_payload']

def response(content=None, finish='stop', **extra):
    return {'choices': [{'finish_reason': finish, 'message': {'content': content, **extra}}]}

class ResponseTests(unittest.TestCase):
    def test_provider_options_are_scoped(self):
        config = {'provider':'remote', 'base_url':'https://api.deepseek.com/v1', 'model':'deepseek-v4-pro', 'local_model':'local'}
        payload = chat_payload(config, [], 8192)
        self.assertEqual(payload['thinking'], {'type':'disabled'})
        self.assertEqual(payload['max_tokens'], 8192)
        for url in ('https://example.com', 'https://api.deepseek.com.example.com'):
            self.assertNotIn('thinking', chat_payload({**config, 'base_url':url}, [], 256))
        self.assertNotIn('thinking', chat_payload({**config, 'provider':'local'}, [], 256))

    def test_text(self):
        self.assertEqual(response_text(response(' OK ')), 'OK')

    def test_content_blocks(self):
        self.assertEqual(response_text(response([{'type': 'text', 'text': 'OK'}])), 'OK')

    def test_empty_is_not_success(self):
        for value in (None, '', '  ', []):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, '正文为空'):
                response_text(response(value))

    def test_truncated_even_with_text(self):
        with self.assertRaisesRegex(RuntimeError, '长度上限'):
            response_text(response('partial JSON', 'length'))

    def test_reasoning_not_exposed(self):
        with self.assertRaisesRegex(RuntimeError, '没有最终回答') as error:
            response_text(response(reasoning_content='private reasoning'))
        self.assertNotIn('private reasoning', str(error.exception))

    def test_refusal_and_tools(self):
        for value in (response(finish='content_filter'), response(refusal='no'), response(finish='tool_calls')):
            with self.assertRaises(RuntimeError):
                response_text(value)

    def test_malformed(self):
        for value in ([], {}, {'choices': []}, {'choices': [None]}, response(42), response([{'type': 'image'}])):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, '格式不兼容'):
                response_text(value)

if __name__ == '__main__':
    unittest.main()
