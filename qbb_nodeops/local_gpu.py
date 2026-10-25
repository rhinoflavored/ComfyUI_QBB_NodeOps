"""Conservative local NVIDIA driver/architecture preflight, without importing torch."""
import re
import subprocess


def detect():
    try:
        def run(*args):
            return subprocess.run(['nvidia-smi', *args], capture_output=True, text=True,
                                  errors='replace', timeout=10, check=True).stdout
        header = run()
        maximum = re.search(r'CUDA\s+(?:UMD\s+)?Version:\s*(\d+)\.(\d+)', header)
        rows = run('--query-gpu=name,compute_cap', '--format=csv,noheader,nounits')
        devices = []
        for line in rows.splitlines():
            name, capability = line.rsplit(',', 1)
            devices.append({'name': name.strip(), 'capability': float(capability.strip())})
        if not maximum or not devices:
            raise ValueError('无法识别显卡或驱动支持的 CUDA 版本')
        return {'devices': devices, 'cuda_max': int(maximum[1]) * 10 + int(maximum[2]),
                'message': '本机显卡：' + '、'.join(d['name'] for d in devices)}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {'devices': [], 'cuda_max': 0,
                'message': '未能确认本机 NVIDIA 显卡和驱动，暂仅提供 CPU；请检查驱动后刷新。'}


def supports(build, gpu):
    if build == 'cpu':
        return True
    if not re.fullmatch(r'cu\d+', build) or not gpu['devices']:
        return False
    cuda = int(build[2:])
    if cuda > gpu['cuda_max']:
        return False
    # Restrict to known architecture families; installation still runs a CUDA test.
    for device in gpu['devices']:
        cc = device['capability']
        if cc < 5.2 or cc > 12.1:
            return False
        if cc >= 10 and cuda < 128:
            return False
        if cc < 7.5 and cuda >= 128:
            return False
    return True


def filter_catalog(data, gpu):
    return {**data, 'hardware': gpu,
            'builds': [b for b in data['builds'] if supports(b, gpu)],
            'combinations': [r for r in data['combinations'] if supports(r['build'], gpu)
                             and (r['build'] == 'cpu' or not any(d['capability'] >= 10 for d in gpu['devices'])
                                  or tuple(map(int, r['torch'].split('.'))) >= (2, 7, 0))]}
