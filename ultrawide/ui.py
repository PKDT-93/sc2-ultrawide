"""The in-game UI: fitting the bottom console to an ultrawide window, and the helper that keeps it fitted.

The bottom console is three 3D models (minimap, unit info, command card) pinned to the left
edge, the center and the right edge and sized by the screen height, so past 16:9 they drift
apart and leave gaps. fix_console() widens the middle model, putting each of its ends where it
would sit at 16:9, measured from its own screen edge. The game rebuilds the console at every
mission load, so `apply` starts a small background helper (run_helper) that refits it each
time. The helper stops when the game closes or the window goes back to 16:9.
"""
import ctypes
import os
import re
import struct
import subprocess
import sys
import time

from .game import Game, is_wide, window_size
from .memory import find_addresses
from .winapi import CREATE_NEW_PROCESS_GROUP, DETACHED_PROCESS, ERROR_ALREADY_EXISTS, SYNCHRONIZE, kernel32, user32

# Struct field offsets inside the game's UI and model classes. These only shift if Blizzard
# changes a class layout, which is rare; a change is caught by the runtime checks (a wrong offset
# makes a name lookup or a plausibility test fail, and nothing is written).
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
ENTRY_SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sc2_ultrawide.py")


def frame_name(game, frame):
    """A UI frame's name from its layout node, or None."""
    try:
        node = game.read_abs_u64(frame + FRAME_LAYOUT_NODE)
        entry = game.read_abs_u64(node + NODE_NAME) if node else 0
        m = IDENT.match(game.read_abs(entry + NAME_CHARS, 0x44)) if entry else None
    except OSError:
        return None
    return m.group(0)[:-1].decode() if m else None


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
        [sys.executable, ENTRY_SCRIPT, HELPER_FLAG],
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
