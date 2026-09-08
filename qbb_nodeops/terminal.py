from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Iterator


class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    BRIGHT_RED = "\033[91m"
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    YELLOW = "\033[33m"
    BRIGHT_YELLOW = "\033[93m"
    MAGENTA = "\033[35m"
    DIM = "\033[2m"


def enable_windows_ansi() -> None:
    """Force-enable VT/ANSI on Windows console (ComfyUI often has isatty()==False)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        for std_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(std_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _c(text: str, *codes: str) -> str:
    return f"{''.join(codes)}{text}{Ansi.RESET}"


def _safe_print(text: str, *codes: str) -> None:
    colored = _c(text, *codes) if codes else text
    try:
        print(colored, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        plain = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        try:
            print(_c(plain, *codes) if codes else plain, flush=True)
        except Exception:
            print(plain, flush=True)


def _to_fullwidth(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        elif ch == " ":
            out.append("\u3000")
        else:
            out.append(ch)
    return "".join(out)


@contextmanager
def _windows_large_bold_font(size_y: int = 28, weight: int = 700) -> Iterator[bool]:
    """
    Temporarily enlarge + bold the Windows console font.

    ANSI cannot change font size per line in cmd.exe. This uses the Win32
    console font API, then restores the previous font in finally.
    Returns whether the switch succeeded.
    """
    if sys.platform != "win32":
        yield False
        return

    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        yield False
        return

    LF_FACESIZE = 32

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class CONSOLE_FONT_INFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("nFont", wintypes.DWORD),
            ("dwFontSize", COORD),
            ("FontFamily", wintypes.UINT),
            ("FontWeight", wintypes.UINT),
            ("FaceName", wintypes.WCHAR * LF_FACESIZE),
        ]

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE

    old = CONSOLE_FONT_INFOEX()
    old.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
    if not kernel32.GetCurrentConsoleFontEx(handle, False, ctypes.byref(old)):
        yield False
        return

    # Keep a deep-enough copy for restore
    restore = CONSOLE_FONT_INFOEX()
    ctypes.memmove(ctypes.byref(restore), ctypes.byref(old), ctypes.sizeof(CONSOLE_FONT_INFOEX))

    new = CONSOLE_FONT_INFOEX()
    ctypes.memmove(ctypes.byref(new), ctypes.byref(old), ctypes.sizeof(CONSOLE_FONT_INFOEX))
    new.cbSize = ctypes.sizeof(CONSOLE_FONT_INFOEX)
    # Keep X=0 so Windows picks a matching width for TrueType fonts
    new.dwFontSize.X = 0
    new.dwFontSize.Y = ctypes.c_short(max(int(old.dwFontSize.Y or 16) + 12, size_y))
    new.FontWeight = weight

    ok = bool(kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(new)))
    try:
        yield ok
    finally:
        try:
            kernel32.SetCurrentConsoleFontEx(handle, False, ctypes.byref(restore))
        except Exception:
            pass


def print_plugin_separator() -> None:
    _safe_print("=" * 72, Ansi.DIM)


def print_plugin_header(name: str, path: str) -> None:
    """
    Print a high-visibility plugin title.

    Why previous attempts looked "not bigger / not bold":
    - Classic Windows cmd cannot change font size for a single line via ANSI.
    - ANSI "bold" often only brightens color, it does not thicken glyphs.
    So we temporarily switch the console font size/weight around the title.
    """
    print("\n", flush=True)
    print_plugin_separator()
    _safe_print("  LOADING", Ansi.DIM)

    with _windows_large_bold_font(size_y=32, weight=700) as enlarged:
        if enlarged:
            # Real larger + bold console font for this title line
            _safe_print(f"  {name}", Ansi.BOLD, Ansi.BRIGHT_YELLOW)
        else:
            # Fallback when Win32 font API is unavailable (piped output / some hosts)
            _safe_print(f"  {_to_fullwidth(name)}", Ansi.BOLD, Ansi.BRIGHT_YELLOW)
            _safe_print(f"  >>> {name} <<<", Ansi.BOLD, Ansi.BRIGHT_YELLOW)

    _safe_print(f"  {path}", Ansi.DIM)
    print(flush=True)


def print_success(name: str, elapsed_sec: float) -> None:
    _safe_print(f"  [OK]  {name}  ({elapsed_sec:.2f}s)", Ansi.BRIGHT_GREEN, Ansi.BOLD)
    print(flush=True)


def print_partial(name: str, elapsed_sec: float, issues: list[str]) -> None:
    _safe_print(
        f"  [PARTIAL]  {name}  ({elapsed_sec:.2f}s)  -- package loaded, but some nodes/modules failed",
        Ansi.BRIGHT_YELLOW,
        Ansi.BOLD,
    )
    _safe_print(f"  Detected {len(issues)} internal issue(s):", Ansi.BRIGHT_YELLOW, Ansi.BOLD)
    for i, issue in enumerate(issues, 1):
        _safe_print(f"  ---- issue #{i} ----", Ansi.MAGENTA, Ansi.BOLD)
        for line in issue.rstrip().splitlines():
            _safe_print(f"  {line}", Ansi.YELLOW)
    _safe_print("  ---------------------", Ansi.MAGENTA, Ansi.BOLD)
    print(flush=True)


def print_failure(name: str, elapsed_sec: float, reason: str, traceback_text: str) -> None:
    _safe_print(f"  [FAILED]  {name}  ({elapsed_sec:.2f}s)", Ansi.BRIGHT_RED, Ansi.BOLD)
    if reason:
        _safe_print(f"  Reason: {reason}", Ansi.BRIGHT_RED, Ansi.BOLD)
    if traceback_text:
        _safe_print("  ---- Traceback ----", Ansi.BRIGHT_RED, Ansi.BOLD)
        for line in traceback_text.rstrip().splitlines():
            _safe_print(f"  {line}", Ansi.RED)
        _safe_print("  -------------------", Ansi.BRIGHT_RED, Ansi.BOLD)
    print(flush=True)


def print_summary(
    success_count: int,
    failure_count: int,
    partial_count: int,
    total_elapsed: float,
) -> None:
    print("\n", flush=True)
    print_plugin_separator()
    _safe_print("  QBB LoadHighlight SUMMARY", Ansi.BOLD, Ansi.BRIGHT_YELLOW)
    _safe_print(
        f"  ok={success_count}  partial={partial_count}  failed={failure_count}  total_time={total_elapsed:.2f}s",
        Ansi.BOLD,
    )
    if failure_count:
        _safe_print(f"  !! {failure_count} package(s) FAILED to load", Ansi.BRIGHT_RED, Ansi.BOLD)
    if partial_count:
        _safe_print(
            f"  !! {partial_count} package(s) loaded with INTERNAL node/module failures",
            Ansi.BRIGHT_YELLOW,
            Ansi.BOLD,
        )
    print_plugin_separator()
    print(flush=True)
