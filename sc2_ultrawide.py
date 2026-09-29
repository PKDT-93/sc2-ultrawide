"""
StarCraft II caps its window and back buffer at a 1366:768 (~1.7786) aspect ratio. That
cap is a single read-only constant in SC2_x64.exe, but the executable maps its code and
constants as a copy-protected view whose maximum page protection is execute-read, so an
external process cannot make the constant writable (Windows rejects the protection change).

The window-size clamp that enforces the cap has an early-out: it skips clamping when the
engine's built-in client-API render path is active. That path is gated by two flags in the
writable .data section (the `listen` option and `gameStateRender`). This tool sets those two
flags for a few milliseconds, resizes the game window to fill the monitor so the engine
accepts the new size, then restores the flags. Nothing on disk changes and the widened size
is gone when the game closes.

The bottom console is three 3D models (minimap, unit info, command card) pinned to the left
edge, the center and the right edge and sized by the screen height, so past 16:9 they drift
apart and leave gaps. The game rebuilds the console at every mission load, so `apply` also
starts a small background helper that widens the middle model each time, putting each of its
ends where it would sit at 16:9, measured from its own screen edge. The helper stops when the
game closes or the window goes back to 16:9.

Commands:
    python sc2_ultrawide.py status      show build, window size, widescreen and console state
    python sc2_ultrawide.py apply       widen the window and keep the bottom console framed
    python sc2_ultrawide.py revert      resize the window back to 16:9 and put the console back

Only Windowed (Fullscreen) display mode is affected. Exclusive fullscreen keeps its own,
separate 16:9 clamp that this tool does not touch.

Read the README before using this. It changes a running, online, anti-cheat-scanned client,
and you accept that risk yourself.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import re
import struct
import subprocess
import sys
import time

GAME_EXE = "SC2_x64.exe"
BUILD = "97563"
ORIGINAL_MAX_ASPECT = struct.unpack("<f", struct.pack("<I", 0x3FE3AAAB))[0]  # 1366 / 768

# Read-only aspect-ratio cap, found by signature (build independent). Used for `status` only;
# it lives in copy-protected memory and cannot be written from another process.
CAP_SIGNATURES = [
    ("window size clamp", "F3 0F 10 0D ?? ?? ?? ?? B8 00 04 00 00 F3 0F 10 15", 4, 8),
    ("fullscreen clamp", "8B 41 24 0F 57 C9 F3 0F 10 15 ?? ?? ?? ?? 0F 57 C0 F3 48 0F 2A C0", 10, 14),
    ("resolution list filter",
     "0F 29 B4 24 D0 00 00 00 F3 0F 10 35 ?? ?? ?? ?? 0F 29 BC 24 C0 00 00 00 F3 0F 10 3D", 12, 16),
]

# Writable .data addresses for build 97563. Guarded at runtime by the option-name check below,
# so a different build (where these move) fails safe instead of writing to the wrong place.
LISTEN_OBJECT = 0x3A0DB10          # engine option "listen"; its value dword sits at +0x70
LISTEN_VALUE = 0x3A0DB80
RENDER_OBJECT = 0x43CB460          # engine option "gameStateRender"; its value byte at +0x61
RENDER_VALUE = 0x43CB4C1
STORED_WIDTH = 0x581452C           # last window client width the engine accepted
STORED_HEIGHT = 0x581039C

# Bottom console, build 97563. These are heap objects rebuilt at every mission load, so they are
# looked up fresh each time and checked by vtable before anything is written.
GAMEUI_POINTER = 0x4032368         # .data pointer to the game UI object
GAMEUI_CONSOLE_PANEL = 0xC60       # game UI -> ConsolePanel frame
PANEL_CENTER_MODEL = 0x138         # ConsolePanel -> model frame that draws the middle console piece
MODEL_FRAME_VTABLE = 0x2ED5410
MODEL_FRAME_FLAGS = 0x214
TRANSFORMS_DIRTY = 0x04            # model frame flag: rebuild model transforms on the next frame
MODEL_FRAME_BUCKETS = 0x1F0        # model list: bucket count here, bucket array pointer at +0x10
MODEL_ENTRY_VTABLE = 0x2ED5400
ENTRY_INSTANCE, ENTRY_POSITION, ENTRY_SCALE = 0x20, 0x28, 0x34
INSTANCE_MODEL, MODEL_BOUNDS = 0x150, 0x430   # instance -> model data -> min(x,y,z), max(x,y,z)
CONSOLE_DEFAULT = (0.0, 1.0)       # middle piece position.x and scale.x in the console skins

HELPER_FLAG = "--console-helper"   # internal: run as the background console helper
HELPER_MUTEX = "sc2_ultrawide_console_helper"

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
PAGE_READWRITE = 0x04
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_ASYNCWINDOWPOS = 0x4000
MONITOR_DEFAULTTONEAREST = 2
SYNCHRONIZE = 0x00100000
ERROR_ALREADY_EXISTS = 183
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD), ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD), ("hModule", wt.HMODULE), ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * 260),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Module32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.ReadProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
kernel32.VirtualProtectEx.argtypes = [
    wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.OpenMutexW.restype = wt.HANDLE
kernel32.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.MonitorFromWindow.restype = wt.HANDLE
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]


class Game:
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

    def read_abs(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t()
        if not kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buf.raw[: got.value]

    def read(self, rva, size):
        return self.read_abs(self.base + rva, size)

    def read_u32(self, rva):
        return struct.unpack("<I", self.read(rva, 4))[0]

    def read_abs_u64(self, addr):
        return struct.unpack("<Q", self.read_abs(addr, 8))[0]

    def write_abs(self, addr, data):
        """Write to heap memory, which is writable without a protection change."""
        done = ctypes.c_size_t()
        if not kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(addr), data, len(data), ctypes.byref(done)):
            raise ctypes.WinError(ctypes.get_last_error())

    def write(self, rva, data):
        addr = ctypes.c_void_p(self.base + rva)
        old = wt.DWORD()
        if not kernel32.VirtualProtectEx(self.handle, addr, len(data), PAGE_READWRITE, ctypes.byref(old)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            done = ctypes.c_size_t()
            if not kernel32.WriteProcessMemory(self.handle, addr, data, len(data), ctypes.byref(done)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            restored = wt.DWORD()
            kernel32.VirtualProtectEx(self.handle, addr, len(data), old.value, ctypes.byref(restored))

    def text_section(self):
        headers = self.read(0, 0x1000)
        e_lfanew = struct.unpack_from("<I", headers, 0x3C)[0]
        count = struct.unpack_from("<H", headers, e_lfanew + 6)[0]
        opt_size = struct.unpack_from("<H", headers, e_lfanew + 20)[0]
        table = e_lfanew + 24 + opt_size
        for i in range(count):
            off = table + 40 * i
            if headers[off: off + 5] == b".text":
                vsize, rva = struct.unpack_from("<II", headers, off + 8)
                return rva, vsize
        raise RuntimeError("no .text section found")

    def locate_cap(self):
        """Return (rva, sites) for the read-only aspect cap, found via its code references."""
        text_rva, text_size = self.text_section()
        code = b"".join(self.read(text_rva + off, min(0x400000, text_size - off))
                        for off in range(0, text_size, 0x400000))
        sites, targets = {}, set()
        for name, pattern, disp_off, next_off in CAP_SIGNATURES:
            rx = re.compile(b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)]))
                                     for t in pattern.split()), re.DOTALL)
            matches = [m.start() for m in rx.finditer(code)]
            if len(matches) != 1:
                continue
            m = matches[0]
            disp = struct.unpack_from("<i", code, m + disp_off)[0]
            sites[name] = text_rva + m
            targets.add(text_rva + m + next_off + disp)
        if len(targets) == 1:
            return targets.pop(), sites
        return None, sites

    def option_name(self, rva):
        """Read an engine option object's name (inline for short names, else via a heap pointer)."""
        obj = self.read(rva, 0x20)
        length = struct.unpack_from("<I", obj, 8)[0] >> 2
        inline = obj[0x10: 0x10 + length]
        if 0 < length <= 8 and all(0x20 <= c < 0x7F for c in inline):
            return inline.decode()
        ptr = struct.unpack_from("<Q", obj, 0x10)[0]
        if not ptr or not 0 < length < 64:
            return ""
        try:
            return self.read_abs(ptr, length).decode("ascii", errors="replace")
        except OSError:
            return ""

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


def check_build(game):
    if game.build != BUILD:
        raise SystemExit(
            f"This build is {game.build}; the writable addresses in this tool are for {BUILD}. "
            f"Update them for your build before applying.")
    names = game.option_name(LISTEN_OBJECT), game.option_name(RENDER_OBJECT)
    if names != ("listen", "gameStateRender"):
        raise SystemExit(f"Option layout check failed (found {names}); not writing to memory.")


def set_window(game, hwnd, width, height, bypass, timeout=10.0):
    """Resize the game window. With bypass=True the aspect clamp is skipped for the resize.

    Returns (accepted, milliseconds_flags_were_set). `accepted` means the engine stored the
    exact size requested.
    """
    mx, my, mw, mh = monitor_rect(hwnd)
    x, y = mx + (mw - width) // 2, my + (mh - height) // 2
    orig_listen = game.read(LISTEN_VALUE, 16)
    orig_render = game.read(RENDER_VALUE, 1)
    t0 = time.perf_counter()
    accepted = False
    try:
        if bypass:
            game.write(LISTEN_VALUE + 8, b"x\0")
            game.write(LISTEN_VALUE, struct.pack("<I", 0x05))
            game.write(RENDER_VALUE, b"\x01")
        user32.SetWindowPos(hwnd, None, x, y, width, height,
                            SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)
        while time.perf_counter() - t0 < timeout:
            if (game.read_u32(STORED_WIDTH), game.read_u32(STORED_HEIGHT)) == (width, height):
                accepted = True
                break
            time.sleep(0.002)
    finally:
        if bypass:
            game.write(LISTEN_VALUE, orig_listen[:4])
            game.write(LISTEN_VALUE + 8, orig_listen[8:])
            game.write(RENDER_VALUE, orig_render)
    return accepted, (time.perf_counter() - t0) * 1000


def is_wide(hwnd):
    cw, ch = window_size(hwnd)
    return bool(ch) and cw / ch > ORIGINAL_MAX_ASPECT + 0.01


def console_target(width, height, min_x, max_x):
    """Position.x and scale.x that put the middle console piece's ends where they sit at 16:9.

    The console models are drawn in 4:3 units (one unit is 2/3 of the screen height) at positions
    in normalised screen units (-1 is the left edge, +1 the right edge). Keeping each end at its
    16:9 distance from its own screen edge makes the side pieces overlap it exactly as at 16:9.
    """
    w16 = 16 / 9 * height
    if width <= w16 + 0.5:
        return CONSOLE_DEFAULT
    unit = 2 / 3 * height
    left = w16 / 2 + min_x * unit
    right = width - w16 / 2 + max_x * unit
    scale = (right - left) / ((max_x - min_x) * unit)
    return (left - min_x * scale * unit) / (width / 2) - 1, scale


def console_plan(game, hwnd, restore=False):
    """Return (frame, entry, current, wanted) for the middle console piece, or None outside a mission.

    `current` and `wanted` are (position.x, scale.x) pairs.
    """
    try:
        ui = struct.unpack("<Q", game.read(GAMEUI_POINTER, 8))[0]
        panel = game.read_abs_u64(ui + GAMEUI_CONSOLE_PANEL)
        frame = game.read_abs_u64(panel + PANEL_CENTER_MODEL) & ~1
        if game.read_abs_u64(frame) != game.base + MODEL_FRAME_VTABLE:
            return None
        count = struct.unpack("<I", game.read_abs(frame + MODEL_FRAME_BUCKETS, 4))[0]
        buckets = game.read_abs_u64(frame + MODEL_FRAME_BUCKETS + 0x10)
        if not buckets or not 0 < count <= 256:
            return None
        nodes = [n for n in struct.unpack(f"<{count}Q", game.read_abs(buckets, 8 * count)) if n]
        if len(nodes) != 1:
            return None
        entry = nodes[0] - 0x10
        if game.read_abs_u64(entry) != game.base + MODEL_ENTRY_VTABLE:
            return None
        model = game.read_abs_u64(game.read_abs_u64(entry + ENTRY_INSTANCE) + INSTANCE_MODEL)
        box = struct.unpack("<6f", game.read_abs(model + MODEL_BOUNDS, 24))
        current = (struct.unpack("<f", game.read_abs(entry + ENTRY_POSITION, 4))[0],
                   struct.unpack("<f", game.read_abs(entry + ENTRY_SCALE, 4))[0])
    except (OSError, struct.error):
        return None
    min_x, max_x = box[0], box[3]
    width, height = window_size(hwnd)
    if not (-3 < min_x < 0 < max_x < 3) or not height:
        return None
    wanted = CONSOLE_DEFAULT if restore else console_target(width, height, min_x, max_x)
    return frame, entry, current, wanted


def same(a, b):
    return all(abs(x - y) < 1e-4 for x, y in zip(a, b))


def fix_console(game, hwnd, restore=False):
    """Fit the middle console piece to the window, or put it back. Returns the values written, or None."""
    plan = console_plan(game, hwnd, restore)
    if not plan or same(plan[2], plan[3]):
        return None
    frame, entry, _, (pos_x, scale_x) = plan
    writer = Game(write=True)
    try:
        writer.write_abs(entry + ENTRY_SCALE, struct.pack("<f", scale_x))
        writer.write_abs(entry + ENTRY_POSITION, struct.pack("<f", pos_x))
        flags = writer.read_abs(frame + MODEL_FRAME_FLAGS, 1)[0]
        writer.write_abs(frame + MODEL_FRAME_FLAGS, bytes([flags | TRANSFORMS_DIRTY]))
    finally:
        writer.close()
    return pos_x, scale_x


def console_state(game, hwnd):
    plan = console_plan(game, hwnd)
    if not plan:
        return "not in a mission"
    _, _, current, wanted = plan
    if same(current, wanted):
        return "normal" if same(wanted, CONSOLE_DEFAULT) else f"framed (middle piece widened {current[1]:.2f}x)"
    if same(current, CONSOLE_DEFAULT):
        return "gaps between the pieces (run apply)"
    return "widened for a different window size (run apply or revert)"


def helper_running():
    handle = kernel32.OpenMutexW(SYNCHRONIZE, False, HELPER_MUTEX)
    if handle:
        kernel32.CloseHandle(handle)
    return bool(handle)


def start_helper():
    """Start the console helper as a detached background process with no window."""
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), HELPER_FLAG],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_helper():
    """Refit the bottom console after every mission load while the window is wide.

    Stops when the game closes or the window is no longer wide (after `revert`, or when the game
    enforces 16:9 again, in which case the console is put back first). A named mutex keeps it to
    one copy.
    """
    ctypes.set_last_error(0)
    mutex = kernel32.CreateMutexW(None, False, HELPER_MUTEX)
    if not mutex:
        return
    try:
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            return
        game = Game()
        try:
            hwnd = game.window()
            while hwnd and user32.IsWindow(hwnd) and is_wide(hwnd):
                try:
                    fix_console(game, hwnd)
                except (Exception, SystemExit):
                    pass
                time.sleep(1.0)
            if hwnd and user32.IsWindow(hwnd):
                fix_console(game, hwnd)
        finally:
            game.close()
    finally:
        kernel32.CloseHandle(mutex)


def print_status(game):
    hwnd = game.window()
    cap_rva, sites = game.locate_cap()
    cap = struct.unpack("<f", game.read(cap_rva, 4))[0] if cap_rva else None
    sw, sh = game.read_u32(STORED_WIDTH), game.read_u32(STORED_HEIGHT)
    print(f"StarCraft II build {game.build}, pid {game.pid}")
    if cap_rva:
        print(f"aspect cap: {cap:.4f} at SC2_x64.exe+0x{cap_rva:X} (read-only; via {', '.join(sites)})")
    if hwnd:
        cw, ch = window_size(hwnd)
        _, _, mw, mh = monitor_rect(hwnd)
        print(f"window: {cw}x{ch} on a {mw}x{mh} monitor  ->  widescreen {'ON' if is_wide(hwnd) else 'off'}")
    print(f"engine stored size: {sw}x{sh}")
    if hwnd and game.build == BUILD:
        print(f"bottom console: {console_state(game, hwnd)}")
    print(f"console helper: {'running' if helper_running() else 'not running'}")


def main():
    ctypes.windll.user32.SetProcessDPIAware()
    if sys.argv[1:] == [HELPER_FLAG]:
        run_helper()
        return
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show build, window size, widescreen and console state")
    sub.add_parser("apply", help="widen the window and keep the bottom console framed until the game closes")
    sub.add_parser("revert", help="resize the window back to 16:9 and put the console back")
    args = parser.parse_args()

    game = Game(write=args.cmd == "apply")
    try:
        if args.cmd == "status":
            print_status(game)
            return
        hwnd = game.window()
        if not hwnd:
            raise SystemExit("Game window not found.")
        check_build(game)
        _, _, mw, mh = monitor_rect(hwnd)
        if args.cmd == "apply":
            if game.read_u32(0x3A0DEA4) != 1:
                print("note: set Display Mode to Windowed (Fullscreen) for this to hold.")
            accepted, ms = set_window(game, hwnd, mw, mh, bypass=True)
            cw, ch = window_size(hwnd)
            print(f"apply: window {cw}x{ch} ({'ok' if accepted else 'not accepted'}), "
                  f"flags set for {ms:.0f} ms")
            if helper_running():
                print("apply: the console helper is already running.")
            elif is_wide(hwnd):
                start_helper()
                print("apply: a background helper keeps the bottom console framed until StarCraft II closes.")
        elif args.cmd == "revert":
            width = round(mh * ORIGINAL_MAX_ASPECT)
            accepted, _ = set_window(game, hwnd, width, mh, bypass=False, timeout=3.0)
            cw, ch = window_size(hwnd)
            print(f"revert: window {cw}x{ch} (16:9)")
            if fix_console(game, hwnd, restore=True):
                print("revert: bottom console put back")
    finally:
        game.close()


if __name__ == "__main__":
    main()
