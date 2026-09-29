"""The game handle: the running StarCraft II process, its module and its window.

StarCraft II caps its window and back buffer at a 1366:768 (~1.7786) aspect ratio. That
cap is a single read-only constant in SC2_x64.exe, but the executable maps its code and
constants as a copy-protected view whose maximum page protection is execute-read, so an
external process cannot make the constant writable (Windows rejects the protection change).

The window-size clamp that enforces the cap has an early-out: it skips clamping when the
engine's built-in client-API render path is active. That path is gated by two flags in the
writable .data section (the `listen` option and `gameStateRender`). set_window() sets those two
flags for a few milliseconds, resizes the game window so the engine accepts the new size, then
restores the flags. Nothing on disk changes and the widened size is gone when the game closes.
"""
import ctypes
import ctypes.wintypes as wt
import re
import struct
import time

from .memory import ProcessMemory
from .winapi import (
    INVALID_HANDLE_VALUE, MODULEENTRY32W, MONITOR_DEFAULTTONEAREST, MONITORINFO, PROCESS_QUERY_INFORMATION,
    PROCESS_VM_OPERATION, PROCESS_VM_READ, PROCESS_VM_WRITE, PROCESSENTRY32W, SWP_ASYNCWINDOWPOS,
    SWP_NOACTIVATE, SWP_NOZORDER, TH32CS_SNAPMODULE, TH32CS_SNAPMODULE32, TH32CS_SNAPPROCESS, kernel32,
    user32)

GAME_EXE = "SC2_x64.exe"
ORIGINAL_MAX_ASPECT = struct.unpack("<f", struct.pack("<I", 0x3FE3AAAB))[0]  # 1366 / 768
DISPLAY_WINDOWED = 1               # displaymode value for Windowed (Fullscreen)


class Game(ProcessMemory):
    """A handle onto the running StarCraft II process. RVAs are offsets from the module base."""

    def __init__(self, write=False):
        self.pid = self._find_pid()
        access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
        if write:
            access |= PROCESS_VM_OPERATION | PROCESS_VM_WRITE
        self.handle = kernel32.OpenProcess(access, False, self.pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.base, self.size, self.path = self._find_module()
        self.build = next(iter(re.findall(r"Base(\d+)", self.path)), "unknown")
        self.addr = None
        self._sections = {}

    def close(self):
        kernel32.CloseHandle(self.handle)

    @staticmethod
    def _find_pid():
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.lower() == GAME_EXE.lower():
                    return entry.th32ProcessID
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
        raise SystemExit(f"{GAME_EXE} is not running. Start StarCraft II first.")

    def _find_module(self):
        for _ in range(20):
            snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, self.pid)
            if snap != INVALID_HANDLE_VALUE:
                break
            time.sleep(0.25)
        else:
            raise ctypes.WinError(ctypes.get_last_error())
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        try:
            ok = kernel32.Module32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szModule.lower() == GAME_EXE.lower():
                    return entry.modBaseAddr, entry.modBaseSize, entry.szExePath
                ok = kernel32.Module32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
        raise SystemExit(f"Could not find the {GAME_EXE} module in pid {self.pid}.")

    def window(self):
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid and user32.IsWindowVisible(hwnd):
                rect = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd))
            return True

        user32.EnumWindows(cb, 0)
        found = [f for f in found if f[0] > 0]
        return max(found)[1] if found else None


def monitor_rect(hwnd):
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def window_size(hwnd):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def is_wide(hwnd):
    cw, ch = window_size(hwnd)
    return bool(ch) and cw / ch > ORIGINAL_MAX_ASPECT + 0.01


def set_window(game, hwnd, width, height, bypass, timeout=10.0):
    """Resize the game window. With bypass=True the aspect clamp is skipped for the resize.

    Returns (accepted, milliseconds_flags_were_set). `accepted` means the engine stored the
    exact size requested.
    """
    a = game.addr
    mx, my, mw, mh = monitor_rect(hwnd)
    x, y = mx + (mw - width) // 2, my + (mh - height) // 2
    orig_listen = game.read(a.listen_value, 16)
    orig_render = game.read(a.render_value, 1)
    t0 = time.perf_counter()
    accepted = False
    try:
        if bypass:
            game.write(a.listen_value + 8, b"x\0")
            game.write(a.listen_value, struct.pack("<I", 0x05))
            game.write(a.render_value, b"\x01")
        user32.SetWindowPos(hwnd, None, x, y, width, height,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
        while time.perf_counter() - t0 < timeout:
            if (game.read_u32(a.stored_width), game.read_u32(a.stored_height)) == (width, height):
                accepted = True
                break
            time.sleep(0.002)
    finally:
        if bypass:
            game.write(a.listen_value, orig_listen[:4])
            game.write(a.listen_value + 8, orig_listen[8:])
            game.write(a.render_value, orig_render)
    return accepted, (time.perf_counter() - t0) * 1000
