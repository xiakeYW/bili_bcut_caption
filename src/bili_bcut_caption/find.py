import os
import sys
import string
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

_COMMON_SKIP = {
    '$recycle.bin', 'recovery', '.git', 'node_modules',
    '__pycache__', '.venv', 'venv',
}

_SKIP_DIRS = {
    'win32': _COMMON_SKIP | {
        'windows', 'winnt', 'system', 'system32', 'syswow64',
        'programdata', 'config.msi', 'msocache', 'documents and settings',
    },
    'darwin': _COMMON_SKIP | {
        'system', 'library', 'applications', 'cores',
        '.trash', '.cache', '.local', '.npm', '.ssh',
    },
    'linux': _COMMON_SKIP | {
        'proc', 'sys', 'dev', 'run', 'snap', 'boot',
        'lost+found', '.cache', '.local', '.config',
    },
}.get(sys.platform, _COMMON_SKIP)

# 扫描系统找到有效的必剪数据文件夹 
def find_bcut_drafts() -> str | None:
    roots = _get_scan_roots()

    with ThreadPoolExecutor(max_workers=min(len(roots), 4)) as pool:
        futures = {pool.submit(_scan_dir, root): root for root in roots}
        for future in as_completed(futures):
            result = future.result()
            if result:
                for f in futures:
                    f.cancel()
                return result

    return None


def _get_scan_roots() -> list[Path]:
    if sys.platform == 'win32':
        return [
            Path(f'{letter}:\\')
            for letter in string.ascii_uppercase
            if Path(f'{letter}:\\').exists()
        ]
    home = Path.home()
    candidates = [
        home / 'Desktop',
        home / 'Documents',
        home / 'Downloads',
        Path('/sdcard'),
        Path('/storage/emulated/0'),
    ]
    candidates.extend(sorted(home.glob('.wine*/drive_c/users')))
    roots = [p for p in candidates if p.is_dir()]
    roots.append(Path('/'))
    return roots


def _scan_dir(root: Path) -> str | None:
    target = 'bcut drafts'
    stack = [str(root)]

    while stack:
        current = stack.pop()

        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        if not os.path.isdir(entry.path):
                            continue
                    elif not entry.is_dir(follow_symlinks=False):
                        continue

                    name_lower = entry.name.lower()

                    if name_lower == target:
                        if os.path.isfile(os.path.join(entry.path, 'draftInfo.json')):
                            return entry.path

                    if name_lower in _SKIP_DIRS or entry.name.startswith('$'):
                        continue

                    stack.append(entry.path)

        except (PermissionError, OSError):
            continue

    return None


if __name__ == '__main__':
    result = find_bcut_drafts()
    if result:
        print(f'找到: {result}')
    else:
        print('未找到 Bcut Drafts 文件夹')
