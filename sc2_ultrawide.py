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

None of the memory addresses are hard-coded. Each run finds them in the running game by
recognizing the code that uses them, and reads each setting back by name before writing, so a
game patch does not need a manual update unless it changes that code.

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
BUILD = "97563"                    # the build this was tested on (addresses are found at run time)
ORIGINAL_MAX_ASPECT = struct.unpack("<f", struct.pack("<I", 0x3FE3AAAB))[0]  # 1366 / 768

# Read-only aspect-ratio cap, found by signature (build independent). Used for `status` only;
# it lives in copy-protected memory and cannot be written from another process.
CAP_SIGNATURES = [
    ("window size clamp", "F3 0F 10 0D ?? ?? ?? ?? B8 00 04 00 00 F3 0F 10 15", 4, 8),
    ("fullscreen clamp", "8B 41 24 0F 57 C9 F3 0F 10 15 ?? ?? ?? ?? 0F 57 C0 F3 48 0F 2A C0", 10, 14),
    ("resolution list filter",
     "0F 29 B4 24 D0 00 00 00 F3 0F 10 35 ?? ?? ?? ?? 0F 29 BC 24 C0 00 00 00 F3 0F 10 3D", 12, 16),
]

# The addresses the tool writes to move with every game patch, so they are not hard-coded.
# find_addresses() locates them in the running game by code pattern (see the resolver below) and
# verifies each option by reading its name back, so a build where a pattern no longer matches
# fails safe (the tool refuses to write) instead of writing to the wrong place.

# Struct field offsets inside the game's UI and model classes. These only shift if Blizzard
# changes a class layout, which is rare; a change is caught by the runtime checks (a wrong offset
# makes a name lookup or a plausibility test fail, and nothing is written).
DISPLAY_WINDOWED = 1               # displaymode value for Windowed (Fullscreen)
FRAME_LAYOUT_NODE = 0x08           # UI frame -> its layout node
NODE_NAME = 0x58                   # layout node -> interned name entry
NAME_CHARS = 0x20                  # name entry -> first character of the name
CONSOLE_MIDDLE_FRAME = "InfopanelModel"   # the model frame that draws the middle console piece
MODEL_FRAME_FLAGS = 0x214          # model frame flags dword
TRANSFORMS_DIRTY = 0x04            # ...its bit that makes the engine rebuild the model transform
MODEL_FRAME_BUCKETS = 0x1F0        # model list: bucket count here, bucket array pointer at +0x10
ENTRY_INSTANCE, ENTRY_POSITION, ENTRY_SCALE = 0x20, 0x28, 0x34   # model entry fields
INSTANCE_MODEL, MODEL_BOUNDS = 0x150, 0x430   # instance -> model data -> min(x,y,z), max(x,y,z)
CONSOLE_DEFAULT = (0.0, 1.0)       # middle piece position.x and scale.x in the console skins
IDENT = re.compile(rb"[A-Za-z][A-Za-z0-9_]{1,63}\0")

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

    def section(self, name):
        """Return (rva, bytes) of a whole PE section, read once and cached."""
        if name in self._sections:
            return self._sections[name]
        headers = self.read(0, 0x1000)
        e_lfanew = struct.unpack_from("<I", headers, 0x3C)[0]
        count = struct.unpack_from("<H", headers, e_lfanew + 6)[0]
        opt_size = struct.unpack_from("<H", headers, e_lfanew + 20)[0]
        table = e_lfanew + 24 + opt_size
        for i in range(count):
            off = table + 40 * i
            if headers[off: off + 8].rstrip(b"\0") == name.encode():
                vsize, rva = struct.unpack_from("<II", headers, off + 8)
                data = b"".join(self.read(rva + o, min(0x400000, vsize - o)) for o in range(0, vsize, 0x400000))
                self._sections[name] = (rva, data)
                return self._sections[name]
        raise RuntimeError(f"no {name} section found")

    def locate_cap(self):
        """Return (rva, sites) for the read-only aspect cap, found via its code references."""
        text_rva, code = self.section(".text")
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


class Addresses:
    """Build-specific addresses found in the running game by find_addresses()."""

    def __init__(self, listen_value, render_value, stored_width, stored_height, display_mode):
        self.listen_value, self.render_value = listen_value, render_value
        self.stored_width, self.stored_height = stored_width, stored_height
        self.display_mode = display_mode
        self.console = False       # set once the console addresses below are found
        self.console_error = None
        self.gameui_pointer = self.gameui_console_panel = None
        self.panel_center_model = self.model_entry_vtable = None


def rel32(blob, at):
    """Offset (within blob) that a rel32 / rip-relative operand stored at `at` points to."""
    return at + 4 + struct.unpack_from("<i", blob, at)[0]


def only(what, hits):
    hits = list(hits)
    if len(hits) != 1:
        raise LookupError(f"{what} ({len(hits)} matches, expected 1)")
    return hits[0]


def option_object(game, value_rva, name):
    """The engine option object named `name` that owns the value at value_rva (it sits just before)."""
    for obj in range(value_rva & ~7, ((value_rva - 0x100) & ~7) - 8, -8):
        if game.option_name(obj) == name:
            return obj
    raise LookupError(f"the {name!r} option (no object with that name before its value)")


def resolve_window(game):
    """Addresses used to widen the window. Anchored on the window-size check, which reads them."""
    text_rva, text = game.section(".text")
    # The window-size check: calls the client-API check, then reads the stored width and height
    # and compares the display mode with 1 before it applies the aspect cap.
    m = only("the window-size check", re.finditer(
        rb"\xe8(.{4})\x84\xc0\x74.\xb0\x01\x48\x83\xc4.\x5f\x5e\xc3"
        rb"\x8b\x05(.{4}).{0,48}?\x8b\x05(.{4}).{0,64}?\x83\x3d(.{4})\x01", text, re.DOTALL))
    client_api = text_rva + rel32(text, m.start(1))
    stored_width = text_rva + rel32(text, m.start(2))
    stored_height = text_rva + rel32(text, m.start(3))
    display_mode = text_rva + m.start(4) + 5 + struct.unpack_from("<i", text, m.start(4))[0]

    def at(rva, size):
        return text[rva - text_rva: rva - text_rva + size]

    # The client-API check calls two one-line getters: `listen` (dword) and `gameStateRender` (byte).
    body = re.match(rb"\x48\x83\xec.\xe8(.{4})\x84\xc0\x74.\xe8(.{4})\x84\xc0", at(client_api, 32), re.DOTALL)
    if not body:
        raise LookupError("the client-API check")
    listen_fn = client_api + rel32(at(client_api, 32), body.start(1))
    render_fn = client_api + rel32(at(client_api, 32), body.start(2))
    lg = re.match(rb"\xf7\x05(.{4})\xfc\xff\xff\xff\x0f\x95\xc0\xc3", at(listen_fn, 16), re.DOTALL)
    rg = re.match(rb"\x0f\xb6\x05(.{4})\xc3", at(render_fn, 8), re.DOTALL)
    if not lg or not rg:
        raise LookupError("the listen / gameStateRender getters")
    listen_value = listen_fn + 10 + struct.unpack_from("<i", lg.group(1))[0]
    render_value = render_fn + 7 + struct.unpack_from("<i", rg.group(1))[0]
    for value, name in ((listen_value, "listen"), (render_value, "gameStateRender"), (display_mode, "displaymode")):
        option_object(game, value, name)
    return Addresses(listen_value, render_value, stored_width, stored_height, display_mode)


def resolve_console(game):
    """Addresses used to fit the bottom console. Returns a tuple, or raises LookupError."""
    text_rva, text = game.section(".text")
    rdata_rva, rdata = game.section(".rdata")
    k = rdata.find(b"\0UIContainer/ConsolePanel\0")
    if k < 0:
        raise LookupError("the ConsolePanel layout path")
    path = rdata_rva + k + 1
    # The game UI stores the ConsolePanel frame in a field right after looking up its layout path.
    ref = only("the ConsolePanel path reference",
               (i for i in (m.start() for m in re.finditer(rb"\x48\x8d[\x05\x0d]", text))
                if text_rva + rel32(text, i + 3) == path))
    store = re.search(rb"\x48\x89\x83(.{4})", text[ref + 7: ref + 7 + 0x60], re.DOTALL)
    if not store:
        raise LookupError("the ConsolePanel field")
    panel_field = struct.unpack_from("<I", store.group(1))[0]
    # The game UI global: the rip-relative pointer loaded most often right before that field is read.
    votes = {}
    for m in re.finditer(rb"\x48\x8b[\x05\x0d](.{4}).{0,16}?\x48\x8b[\x88\x89]"
                         + re.escape(struct.pack("<I", panel_field)), text, re.DOTALL):
        g = text_rva + rel32(text, m.start(1))
        votes[g] = votes.get(g, 0) + 1
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < 3 or (len(ranked) > 1 and ranked[1][1] * 2 > ranked[0][1]):
        raise LookupError(f"the game UI pointer (votes {ranked[:3]})")
    gameui = ranked[0][0]
    # The ConsolePanel's three model frames are read in a row, 8 bytes apart; the middle one is ours.
    trip = [(text_rva + m.start(), m) for m in
            re.finditer(rb"\x8b\x93(.{4})\x48\x8b\x8b(.{4})\xe8(.{4})", text, re.DOTALL)]
    middles = set()
    for (a1, m1), (a2, m2), (a3, m3) in zip(trip, trip[1:], trip[2:]):
        if a3 - a1 > 0x80:
            continue
        f = [struct.unpack_from("<I", x.group(2))[0] for x in (m1, m2, m3)]
        e = [struct.unpack_from("<I", x.group(1))[0] for x in (m1, m2, m3)]
        calls = {a + 18 + struct.unpack_from("<i", x.group(3))[0] for a, x in ((a1, m1), (a2, m2), (a3, m3))}
        if f[1] - f[0] == f[2] - f[1] == 8 and e[1] - e[0] == e[2] - e[1] == 4 and len(calls) == 1:
            middles.add(f[1])
    middle = only("the console model fields", middles)
    # A model entry's vtable: stored just before the entry's scale is set to its default of 1.0.
    vtables = set()
    for m in re.finditer(rb"\xc7\x43\x34\x00\x00\x80\x3f\xc7\x43\x38\x00\x00\x80\x3f", text, re.DOTALL):
        window = text[m.start() - 0x40: m.start()]
        leas = list(re.finditer(rb"\x48\x8d\x05(.{4})\x48\x89\x03", window, re.DOTALL))
        if leas:
            vtables.add(text_rva + m.start() - 0x40 + rel32(window, leas[-1].start(1)))
    entry_vtable = only("the model entry vtable", vtables)
    return gameui, panel_field, middle, entry_vtable


def find_addresses(game):
    """Locate every build-specific address in the running game and store them on game.addr.

    The window addresses are required (LookupError if missing). The console addresses are optional:
    if they cannot be found, game.addr.console stays False and the console is left alone.
    """
    game.addr = resolve_window(game)
    try:
        (game.addr.gameui_pointer, game.addr.gameui_console_panel,
         game.addr.panel_center_model, game.addr.model_entry_vtable) = resolve_console(game)
        game.addr.console = True
    except LookupError as e:
        game.addr.console_error = str(e)
    return game.addr


def frame_name(game, frame):
    """A UI frame's name from its layout node, or None."""
    try:
        node = game.read_abs_u64(frame + FRAME_LAYOUT_NODE)
        entry = game.read_abs_u64(node + NODE_NAME) if node else 0
        m = IDENT.match(game.read_abs(entry + NAME_CHARS, 0x44)) if entry else None
    except OSError:
        return None
    return m.group(0)[:-1].decode() if m else None


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
    a = game.addr
    if not a or not a.console:
        return None
    try:
        ui = struct.unpack("<Q", game.read(a.gameui_pointer, 8))[0]
        panel = game.read_abs_u64(ui + a.gameui_console_panel)
        frame = game.read_abs_u64(panel + a.panel_center_model) & ~1
        if frame_name(game, frame) != CONSOLE_MIDDLE_FRAME:
            return None
        count = struct.unpack("<I", game.read_abs(frame + MODEL_FRAME_BUCKETS, 4))[0]
        buckets = game.read_abs_u64(frame + MODEL_FRAME_BUCKETS + 0x10)
        if not buckets or not 0 < count <= 256:
            return None
        nodes = [n for n in struct.unpack(f"<{count}Q", game.read_abs(buckets, 8 * count)) if n]
        if len(nodes) != 1:
            return None
        entry = nodes[0] - 0x10
        if game.read_abs_u64(entry) != game.base + a.model_entry_vtable:
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
            try:
                find_addresses(game)
            except LookupError:
                return
            if not game.addr.console:
                return
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
    print(f"StarCraft II build {game.build}, pid {game.pid}")
    if cap_rva:
        print(f"aspect cap: {cap:.4f} at SC2_x64.exe+0x{cap_rva:X} (read-only; via {', '.join(sites)})")
    if hwnd:
        cw, ch = window_size(hwnd)
        _, _, mw, mh = monitor_rect(hwnd)
        print(f"window: {cw}x{ch} on a {mw}x{mh} monitor  ->  widescreen {'ON' if is_wide(hwnd) else 'off'}")
    try:
        a = find_addresses(game)
    except LookupError as e:
        print(f"writable addresses: not found in this build ({e}); apply and revert will refuse to run")
    else:
        print(f"engine stored size: {game.read_u32(a.stored_width)}x{game.read_u32(a.stored_height)}")
        if hwnd and a.console:
            print(f"bottom console: {console_state(game, hwnd)}")
        elif not a.console:
            print(f"bottom console: fix unavailable in this build ({a.console_error})")
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
        try:
            find_addresses(game)
        except LookupError as e:
            raise SystemExit(f"Could not find the memory this tool needs in build {game.build} ({e}). "
                             f"A game patch may have changed that code; nothing was written.")
        _, _, mw, mh = monitor_rect(hwnd)
        if args.cmd == "apply":
            if game.read_u32(game.addr.display_mode) != DISPLAY_WINDOWED:
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
