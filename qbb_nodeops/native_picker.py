"""Windows Common Item Dialog in-process; no shell or child process required."""
import ctypes
import uuid


def _check(result, stage):
    if result & 0x80000000:
        raise OSError(f'{stage} failed (HRESULT 0x{result & 0xffffffff:08X})')


def _call(pointer, index, types=(), args=()):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
    return method(pointer, *args)


def _show(dialog):
    return _call(dialog, 3, (ctypes.c_void_p,), (None,))


def choose(kind):
    """Called on a fresh STA thread; returns a path only, never opens file content."""
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    _check(ole.CoInitializeEx(None, 2), 'COM initialization')
    dialog, item, path = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid = 'C0B4E2F3-BA21-4773-8DBA-335EC946EB8B' if kind == 'zip-save' else 'DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7'
        clsid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(clsid).bytes_le)
        iid = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('42f85136-db7e-439c-85f1-e4075d135fc8').bytes_le)
        _check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(dialog)), 'Create file dialog')
        flags = ctypes.c_ulong()
        _check(_call(dialog, 10, (ctypes.POINTER(ctypes.c_ulong),), (ctypes.byref(flags),)), 'Read dialog options')
        # Filesystem only, existing parent, no recent-history entry and no test-file creation.
        flags.value |= 0x40 | 0x800 | 0x02000000 | 0x10000
        flags.value &= ~0x200  # single selection
        if kind == 'folder':
            flags.value |= 0x20
        elif kind == 'zip-open':
            flags.value |= 0x1000
        _check(_call(dialog, 9, (ctypes.c_ulong,), (flags.value,)), 'Set dialog options')
        title = 'QBB NodeOps — 选择文件夹' if kind == 'folder' else 'QBB NodeOps — 选择 ZIP 文件' if kind == 'zip-open' else 'QBB NodeOps — ZIP 保存位置'
        _check(_call(dialog, 17, (ctypes.c_wchar_p,), (title,)), 'Set dialog title')
        if kind != 'folder':
            class Filter(ctypes.Structure):
                _fields_ = [('name', ctypes.c_wchar_p), ('pattern', ctypes.c_wchar_p)]
            filters = (Filter * 1)(Filter('ZIP archive', '*.zip'))
            _check(_call(dialog, 4, (ctypes.c_uint, ctypes.POINTER(Filter)), (1, filters)), 'Set ZIP filter')
            _check(_call(dialog, 22, (ctypes.c_wchar_p,), ('zip',)), 'Set extension')
            if kind == 'zip-save':
                _check(_call(dialog, 15, (ctypes.c_wchar_p,), ('QBB-backup.zip',)), 'Set filename')
        result = _show(dialog)
        if result & 0xffffffff == 0x800704C7:  # user cancelled
            return {'path': ''}
        _check(result, 'Show file dialog')
        _check(_call(dialog, 20, (ctypes.POINTER(ctypes.c_void_p),), (ctypes.byref(item),)), 'Read selection')
        _check(_call(item, 5, (ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)),
                     (0x80058000, ctypes.byref(path))), 'Read filesystem path')
        return {'path': ctypes.wstring_at(path)}
    finally:
        if path.value:
            ole.CoTaskMemFree(path)
        if item.value:
            _call(item, 2)
        if dialog.value:
            _call(dialog, 2)
        ole.CoUninitialize()
