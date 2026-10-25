"""Read and restore existing full-environment snapshots. New snapshots are not created."""
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path


def root_dir():
    from .instances import CURRENT_COMFYUI_ROOT
    return Path(CURRENT_COMFYUI_ROOT).parent / 'QBB_environment_snapshots'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def listing():
    entries = []
    for file in root_dir().glob('*/manifest.json'):
        try:
            data = json.loads(file.read_text(encoding='utf-8'))
            if data.get('status') == 'complete':
                entries.append({
                    'id': file.parent.name,
                    'label': data['label'],
                    'created': data['created'],
                    'path': str(file.parent),
                    'packages': len(data['packages']),
                })
        except (OSError, ValueError, KeyError):
            continue
    return {'items': sorted(entries, key=lambda x: x['created'], reverse=True), 'directory': str(root_dir())}


def restore(snapshot):
    snapshot = Path(snapshot).resolve()
    data = json.loads((snapshot / 'manifest.json').read_text(encoding='utf-8'))
    if data.get('status') != 'complete':
        raise ValueError('Incomplete snapshot')
    originals = []
    for entry in data['roots']:
        if entry['key'] not in ('comfyui', 'python', 'base_python'):
            raise ValueError('Invalid root')
        if not Path(entry['original']).is_absolute():
            raise ValueError('Absolute original path required')
        original = Path(entry['original']).resolve()
        if not original.is_absolute() or original == Path(original.anchor) or len(original.parts) < 3:
            raise ValueError('Unsafe root')
        if Path(sys.executable).resolve().is_relative_to(original):
            raise ValueError('Run restore with a separate Python installation, not the environment being restored')
        originals.append(original)
    if len(set(originals)) != len(originals):
        raise ValueError('Duplicate targets')
    if any(a.is_relative_to(b) for a in originals for b in originals if a != b):
        raise ValueError('Overlapping targets')
    print('Will restore these directories:', *[str(p) for p in originals], sep='\n')
    if input('Close ComfyUI and Python processes. Type RESTORE to continue: ') != 'RESTORE':
        return
    for relative, expected in data['files'].items():
        file = (snapshot / relative).resolve()
        if not file.is_relative_to(snapshot) or digest(file) != expected:
            raise ValueError('Snapshot checksum mismatch: ' + relative)
    stamp = str(int(time.time()))
    swaps = []
    try:
        for entry, original in zip(data['roots'], originals):
            staged = original.with_name(original.name + '.qbb-staged-' + stamp)
            backup = original.with_name(original.name + '.qbb-before-restore-' + stamp)
            if staged.exists() or backup.exists():
                raise ValueError('Restore staging already exists')
            shutil.copytree(snapshot / entry['key'], staged)
            keep = (['models', 'output', 'temp', 'user'] if entry['key'] == 'comfyui' else [])
            if original.exists():
                for name in keep:
                    src = original / name
                    if src.exists() and not (staged / name).exists():
                        if src.is_dir():
                            shutil.copytree(src, staged / name, symlinks=True)
                        else:
                            shutil.copy2(src, staged / name)
                original.rename(backup)
            try:
                staged.rename(original)
            except Exception:
                if backup.exists():
                    backup.rename(original)
                raise
            swaps.append((original, backup))
    except Exception:
        for original, backup in reversed(swaps):
            original.rename(original.with_name(original.name + '.qbb-failed-' + stamp))
            if backup.exists():
                backup.rename(original)
        raise
    print('Restore complete. Previous environment directories retained. Restart ComfyUI to verify.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', required=True)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.restore:
        restore(args.snapshot)
