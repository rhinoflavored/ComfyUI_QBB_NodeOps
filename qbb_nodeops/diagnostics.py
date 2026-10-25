"""Deterministic error attribution. No plugin code is executed."""
import re

RULES = [
 ('cuda_memory', '显存不足', 'GPU memory exhausted', r'CUDA out of memory|torch\.OutOfMemoryError|CUBLAS_STATUS_ALLOC_FAILED'),
 ('cuda_compatibility', 'CUDA / Torch 兼容性', 'CUDA / Torch compatibility', r'no kernel image|not compiled with CUDA|CUDA driver version is insufficient|invalid device function'),
 ('binary_dependency', '二进制依赖或 ABI 不兼容', 'Binary dependency / ABI mismatch', r'DLL load failed|undefined symbol|numpy\.dtype size changed|_ARRAY_API|cannot open shared object'),
 ('missing_module', '缺少模块或依赖包', 'Missing module or dependency', r"(?:ModuleNotFoundError:.*)?No module named ['\"]([^'\"]+)"),
 ('api_mismatch', '导入接口或版本不兼容', 'Import API / version mismatch', r'cannot import name|partially initialized module|circular import'),
 ('permission', '文件权限或占用', 'File permissions / lock', r'PermissionError|Permission denied|WinError 32|Access is denied'),
 ('missing_file', '缺少文件或模型', 'Missing file or model', r'FileNotFoundError|No such file or directory'),
 ('network', '网络连接或下载失败', 'Network / download failure', r'ConnectionError|ConnectTimeout|ReadTimeout|CERTIFICATE_VERIFY_FAILED|HTTP Error|ProxyError|Connection refused'),
 ('syntax', '代码语法或 Python 版本问题', 'Syntax / Python compatibility', r'SyntaxError|IndentationError|invalid syntax'),
 ('version_conflict', '依赖版本冲突', 'Dependency version conflict', r'VersionConflict|requires .*version|incompatible version|ResolutionImpossible'),
]


def classify(issue):
    text = '\n'.join([str(issue.get('reason', '')), str(issue.get('traceback', '')),
                      '\n'.join(str(x) for x in (issue.get('partial_issues') or [])[:12])])[-18000:]
    findings = []
    for kind, zh, en, pattern in RULES:
        match = re.search(pattern, text, re.I)
        if match:
            entry = {'kind': kind, 'label_zh': zh, 'label_en': en, 'evidence': match.group(0)[:500]}
            if kind == 'missing_module':
                entry['module'] = match.group(1)
                entry['note'] = 'Import names are not necessarily pip package names; dotted names can be internal modules or version mismatches.'
            findings.append(entry)
    return findings or [{'kind': 'unknown', 'label_zh': '尚未归类', 'label_en': 'Unclassified', 'evidence': str(issue.get('reason', ''))[:500]}]
