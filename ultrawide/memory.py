"""Reading and writing the game's memory, and finding the addresses the tool needs.

None of the memory addresses are hard-coded, because they move with every game patch.
find_addresses() locates them in the running game by recognizing the code that uses them, and
reads each setting back by name before anything is written, so a build where a pattern no longer
matches fails safe (the tool refuses to write) instead of writing to the wrong place. This is
the only file that holds game-code patterns.
"""
import ctypes
import ctypes.wintypes as wt
import re
import struct

from .winapi import PAGE_READWRITE, kernel32

# Read-only aspect-ratio cap, found by signature (build independent). Used for `status` only;
# it lives in copy-protected memory and cannot be written from another process.
CAP_SIGNATURES = [
    ("window size clamp", "F3 0F 10 0D ?? ?? ?? ?? B8 00 04 00 00 F3 0F 10 15", 4, 8),
    ("fullscreen clamp", "8B 41 24 0F 57 C9 F3 0F 10 15 ?? ?? ?? ?? 0F 57 C0 F3 48 0F 2A C0", 10, 14),
    ("resolution list filter",
     "0F 29 B4 24 D0 00 00 00 F3 0F 10 35 ?? ?? ?? ?? 0F 29 BC 24 C0 00 00 00 F3 0F 10 3D", 12, 16),
]


class ProcessMemory:
    """Reads and writes another process's memory. RVAs are offsets from the module base.

    The subclass sets self.handle (an open process handle), self.base (the module base address)
    and self._sections (an empty dict used as the section cache).
    """

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


def locate_cap(game):
    """Return (rva, sites) for the read-only aspect cap, found via its code references."""
    text_rva, code = game.section(".text")
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
