"""
StarCraft II ultrawide unlock for the campaign.

Widens the game window past StarCraft II's 16:9 limit and keeps the bottom console framed on
ultrawide screens. Nothing on disk changes, and closing the game undoes everything. How each
part works is described in the modules under ultrawide/ (game.py for the window, memory.py for
finding the addresses, ui.py for the bottom console) and in the README.

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
import struct
import sys
import time

from ultrawide.game import DISPLAY_WINDOWED, ORIGINAL_MAX_ASPECT, Game, is_wide, monitor_rect, set_window, window_size
from ultrawide.memory import find_addresses, locate_cap
from ultrawide.ui import HELPER_FLAG, console_plan, console_state, fix_console, helper_running, run_helper, start_helper


def print_status(game):
    hwnd = game.window()
    cap_rva, sites = locate_cap(game)
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
    apply_parser = sub.add_parser("apply", help="widen the window and keep the bottom console framed until the game closes")
    apply_parser.add_argument("--no-helper", action="store_true",
                              help="fit the current console once without starting a background helper")
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
                raise SystemExit("Set Display Mode to Windowed (Fullscreen) first; nothing was written.")
            stored = (game.read_u32(game.addr.stored_width), game.read_u32(game.addr.stored_height))
            hud_only = (window_size(hwnd) == (mw, mh)
                        and mh > 0 and mw / mh > ORIGINAL_MAX_ASPECT + 0.01
                        and console_plan(game, hwnd) is not None)
            if hud_only:
                # A mission may clear the fields used by the resize acceptance check.
                # With a validated HUD and an already full-monitor client area, no resize
                # or client-API flag writes are needed before HUD fitting/helper startup.
                accepted, ms = True, 0.0
                print("apply: verified full-monitor window; updating the current HUD only.")
            elif not all(0 < dimension <= 32768 for dimension in stored):
                raise SystemExit("Engine window dimensions are unavailable; wait for the menu or mission. Nothing was written.")
            elif window_size(hwnd) == (mw, mh) and stored == (mw, mh):
                accepted, ms = True, 0.0
            else:
                accepted, ms = set_window(game, hwnd, mw, mh, bypass=True)
            cw, ch = window_size(hwnd)
            print(f"apply: window {cw}x{ch} ({'ok' if accepted else 'not accepted'}), "
                  f"flags set for {ms:.0f} ms")
            if not accepted:
                raise SystemExit("Game did not accept the requested size; no console helper was started.")
            if args.no_helper:
                if fix_console(game, hwnd):
                    print("apply: bottom console fitted once; no background helper started.")
                else:
                    print("apply: no console update needed or available; no background helper started.")
            elif helper_running():
                print("apply: the console helper is already running.")
            elif is_wide(hwnd):
                start_helper()
                # A bundled onefile helper must unpack its own runtime on cold start.
                for _ in range(150 if getattr(sys, "frozen", False) else 20):
                    if helper_running():
                        break
                    time.sleep(0.1)
                if not helper_running():
                    raise SystemExit("Console helper did not become ready; check the helper log.")
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
