"""Local-only Windows chooser, isolated on a dedicated COM apartment thread."""
import ipaddress
import os
import threading

LOCK = threading.Lock()


def is_loopback(address):
    if address is None:
        return False
    text = str(address).strip()
    if text.startswith("[") and "]" in text:
        text = text[1:text.index("]")]
    try:
        return ipaddress.ip_address(text).is_loopback
    except (ValueError, TypeError):
        return text.lower() == "localhost"

def _native_choose(kind):
    from .native_picker import choose
    return choose(kind)


def choose(kind='folder'):
    if kind not in ('folder', 'zip-open', 'zip-save'):
        raise ValueError('Unsupported picker type')
    if os.name != 'nt':
        raise ValueError('Native selection supports Windows only. Enter the path manually.')
    if not LOCK.acquire(blocking=False):
        raise ValueError('文件选择窗口已打开，请先完成选择或关闭该窗口。')
    result, errors = [], []
    def worker():
        try:
            result.append(_native_choose(kind))
        except Exception as exc:
            errors.append(exc)
        finally:
            LOCK.release()
    thread = threading.Thread(target=worker, name='NodeOps-file-picker', daemon=True)
    try:
        thread.start()
    except Exception:
        LOCK.release()
        raise
    thread.join(timeout=180)
    if thread.is_alive():
        raise ValueError('文件选择尚未完成，请先关闭已打开的选择窗口后重试。')
    if errors:
        # Preserve the actual API/error code instead of attributing it to security software.
        raise ValueError('文件选择窗口调用失败：' + str(errors[0])) from errors[0]
    return result[0]
