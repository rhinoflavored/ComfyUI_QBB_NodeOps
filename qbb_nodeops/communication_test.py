"""User-triggered tests of draft settings; never persist configuration."""
import time
from urllib.request import build_opener, ProxyHandler, Request
from . import settings, ai_service


def test_network(patch):
    config = settings._normalize({**settings.load_settings(), **patch})
    proxy = settings.proxy_url(config)
    mode = settings.network_mode(config)
    url = 'https://github.com/Comfy-Org/ComfyUI'
    if mode == 'mirror':
        url = config['mirror_prefix'] + url
    opener = build_opener(ProxyHandler({'http':proxy,'https':proxy} if proxy else {}))
    start = time.monotonic()
    try:
        with opener.open(Request(url, headers={'User-Agent':'QBB-NodeOps'}), timeout=20) as response:
            response.read(1024)
            code = response.status
    except Exception as exc:
        raise ValueError(f'通信失败（{mode}）：{exc}') from exc
    return {'message':f'通信成功（{mode}），GitHub 测试地址返回 HTTP {code}，耗时 {time.monotonic()-start:.1f} 秒。仅验证这条连接，不代表所有网站均可访问。'}


def test_ai(patch):
    config = ai_service.draft_settings(ai_service._load_raw(), patch)
    local = config['provider'] == 'local'
    if not local and not config['api_key']:
        raise ValueError('请填写 API Key；更换地址后测试需重新输入密钥。')
    model = config['local_model'] if local else config['model']
    if not model:
        raise ValueError('请填写模型名称。')
    start = time.monotonic()
    try:
        data = ai_service._json_request((config['local_url'] if local else config['base_url'])+'/chat/completions',
            ai_service.chat_payload(config, [{'role':'user','content':'Reply with OK.'}], 256),
            '' if local else config['api_key'],local=local)
        ai_service.response_text(data)
    except Exception as exc:
        message = str(exc)
        if config['api_key']:
            message = message.replace(config['api_key'],'[REDACTED]')
        raise ValueError(message) from None
    return {'message':f'AI 通信成功，模型 {model} 已返回有效正文，耗时 {time.monotonic()-start:.1f} 秒。此测试不保存设置；修改后请点击“保存 AI 设置”。'}
