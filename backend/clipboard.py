"""Windows clipboard access with correct pointer types and bounded contention."""

import ctypes
import re
import sys
import threading
import time
from ctypes import wintypes

from .config import MAX_INPUT_BYTES


def decode_cf_html(data: bytes) -> str:
    for start_name, end_name in ((b'StartHTML', b'EndHTML'), (b'StartFragment', b'EndFragment')):
        start = re.search(start_name + rb':\s*(-?\d+)', data[:4096])
        end = re.search(end_name + rb':\s*(-?\d+)', data[:4096])
        if start and end and 0 <= int(start[1]) < int(end[1]) <= len(data):
            return data[int(start[1]):int(end[1])].decode('utf-8')
    raise ValueError('클립보드의 HTML 형식을 읽을 수 없습니다. HTML 소스를 직접 붙여넣으세요.')


def choose_clipboard_content(text: str | None, html: str | None) -> dict:
    if text and re.search(r'<(?:div|header|html|section)\b', text, re.IGNORECASE):
        return {'text': text, 'format': 'text'}
    if html:
        return {'text': html, 'format': 'html'}
    return {'text': text or '', 'format': 'text'}


class WindowsClipboard:
    def __init__(self):
        self.lock = threading.Lock()
        if sys.platform != 'win32':
            raise OSError('이 앱의 클립보드 기능은 Windows에서 실행해야 합니다.')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        signatures = [
            (self.user.OpenClipboard, [wintypes.HWND], wintypes.BOOL),
            (self.user.CloseClipboard, [], wintypes.BOOL),
            (self.user.EmptyClipboard, [], wintypes.BOOL),
            (self.user.GetClipboardData, [wintypes.UINT], wintypes.HANDLE),
            (self.user.SetClipboardData, [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE),
            (self.user.RegisterClipboardFormatW, [wintypes.LPCWSTR], wintypes.UINT),
            (self.user.CreateWindowExW, [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID], wintypes.HWND),
            (self.user.DestroyWindow, [wintypes.HWND], wintypes.BOOL),
            (self.kernel.GlobalAlloc, [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL),
            (self.kernel.GlobalLock, [wintypes.HGLOBAL], wintypes.LPVOID),
            (self.kernel.GlobalUnlock, [wintypes.HGLOBAL], wintypes.BOOL),
            (self.kernel.GlobalSize, [wintypes.HGLOBAL], ctypes.c_size_t),
            (self.kernel.GlobalFree, [wintypes.HGLOBAL], wintypes.HGLOBAL),
        ]
        for function, args, result in signatures:
            function.argtypes, function.restype = args, result
        self.html_format = self.user.RegisterClipboardFormatW('HTML Format')

    def _open(self, owner=None):
        for _ in range(10):
            if self.user.OpenClipboard(owner):
                return
            time.sleep(0.05)
        raise OSError('다른 프로그램이 클립보드를 사용 중입니다. 잠시 후 다시 복사하세요.')

    def _bytes(self, kind: int) -> bytes | None:
        handle = self.user.GetClipboardData(kind)
        if not handle:
            return None
        size = self.kernel.GlobalSize(handle)
        if size > MAX_INPUT_BYTES * 2 + 4096:
            raise ValueError('클립보드 입력이 너무 큽니다 (최대 5 MiB).')
        ptr = self.kernel.GlobalLock(handle)
        if not ptr:
            raise OSError('클립보드 메모리를 읽을 수 없습니다.')
        try:
            return ctypes.string_at(ptr, size)
        finally:
            self.kernel.GlobalUnlock(handle)

    def read(self) -> dict:
        with self.lock:
            self._open()
            try:
                data = self._bytes(13)  # CF_UNICODETEXT
                text = data.decode('utf-16-le').split('\0', 1)[0] if data else None
                if text and re.search(r'<(?:div|header|html|section)\b', text, re.I):
                    return choose_clipboard_content(text, None)
                rich = self._bytes(self.html_format)
                return choose_clipboard_content(text, decode_cf_html(rich) if rich else None)
            finally:
                self.user.CloseClipboard()

    def write(self, text: str):
        # A message-only window supplies a valid clipboard owner for SetClipboardData.
        with self.lock:
            owner = self.user.CreateWindowExW(0, 'STATIC', 'SummaryToVideoClipboard', 0,
                                              0, 0, 0, 0, wintypes.HWND(-3), None, None, None)
            if not owner:
                raise OSError('클립보드 쓰기 창을 만들 수 없습니다.')
            handle = None
            opened = False
            try:
                data = text.replace('\r\n', '\n').replace('\n', '\r\n').encode('utf-16-le') + b'\0\0'
                handle = self.kernel.GlobalAlloc(0x0002, len(data))
                if not handle:
                    raise OSError('클립보드 메모리를 할당할 수 없습니다.')
                ptr = self.kernel.GlobalLock(handle)
                if not ptr:
                    raise OSError('클립보드 메모리를 잠글 수 없습니다.')
                try:
                    ctypes.memmove(ptr, data, len(data))
                finally:
                    self.kernel.GlobalUnlock(handle)
                self._open(owner)
                opened = True
                if not self.user.EmptyClipboard() or not self.user.SetClipboardData(13, handle):
                    raise OSError('클립보드 자동 복사에 실패했습니다.')
                handle = None  # Ownership transferred to Windows.
            finally:
                if opened:
                    self.user.CloseClipboard()
                if handle:
                    self.kernel.GlobalFree(handle)
                self.user.DestroyWindow(owner)
