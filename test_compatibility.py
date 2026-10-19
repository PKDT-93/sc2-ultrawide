"""Offline regression tests; no process handles or game memory accesses."""
import struct
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch

from ultrawide.game import Game
from ultrawide.game import set_window
from ultrawide.ui import fix_console
from ultrawide.ui import run_helper
from ultrawide.ui import start_helper, HELPER_FLAG, ENTRY_SCRIPT


class FrozenHelperTests(unittest.TestCase):
    def test_frozen_helper_has_independent_runtime(self):
        with patch.object(sys, 'frozen', True, create=True), patch('ultrawide.ui.subprocess.Popen') as spawn:
            start_helper()
        self.assertEqual(spawn.call_args.args[0], [sys.executable, HELPER_FLAG])
        self.assertEqual(spawn.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_source_helper_keeps_script_entry(self):
        with patch.object(sys, 'frozen', False, create=True), patch('ultrawide.ui.subprocess.Popen') as spawn:
            start_helper()
        self.assertEqual(spawn.call_args.args[0], [sys.executable, ENTRY_SCRIPT, HELPER_FLAG])
from ultrawide.memory import CAP_SIGNATURES, ProcessMemory, locate_cap, find_model_entry_vtable


class FakeMemory:
    def __init__(self, value):
        # The legacy filter template also matches -1.0 in 98370: reject that case.
        pattern = CAP_SIGNATURES[-1][1]
        self.code = bytearray(int(t, 16) if t != '??' else 0 for t in pattern.split())
        struct.pack_into('<i', self.code, 12, 0x2000 - 0x1000 - 16)
        self.value = value

    def section(self, name):
        return 0x1000, bytes(self.code)

    def read(self, rva, count):
        if (rva, count) != (0x2000, 4):
            raise AssertionError('unexpected read')
        return struct.pack('<f', self.value)


class CompatibilityTests(unittest.TestCase):
    def test_helper_stops_when_game_window_disappears(self):
        game = unittest.mock.Mock(pid=12345, build='98370')
        with patch('ultrawide.ui.kernel32.CreateMutexW', return_value=1), \
             patch('ultrawide.ui.kernel32.CloseHandle') as close_mutex, \
             patch('ultrawide.ui.Game', return_value=game), \
             patch('ultrawide.ui.find_addresses'), \
             patch('ultrawide.ui.user32.IsWindow', side_effect=[True, False, False]), \
             patch('ultrawide.ui.is_wide', return_value=True), \
             patch('ultrawide.ui.fix_console', return_value=(0.06, 2.2)) as fit, \
             patch('ultrawide.ui.helper_log') as log, \
             patch('ultrawide.ui.time.sleep'):
            run_helper()
        fit.assert_called_once()
        game.close.assert_called_once()
        close_mutex.assert_called_once_with(1)
        self.assertTrue(any('HUD fitted' in call.args[0] for call in log.call_args_list))

    def test_helper_stops_on_write_error_instead_of_retrying_forever(self):
        game = unittest.mock.Mock(pid=12345, build='98370')
        with patch('ultrawide.ui.kernel32.CreateMutexW', return_value=1), \
             patch('ultrawide.ui.kernel32.CloseHandle'), \
             patch('ultrawide.ui.Game', return_value=game), \
             patch('ultrawide.ui.find_addresses'), \
             patch('ultrawide.ui.user32.IsWindow', return_value=True), \
             patch('ultrawide.ui.is_wide', return_value=True), \
             patch('ultrawide.ui.fix_console', side_effect=OSError('injected failure')) as fit, \
             patch('ultrawide.ui.helper_log') as log:
            run_helper()
        fit.assert_called_once()
        game.close.assert_called_once()
        self.assertTrue(any('stopping after OSError' in call.args[0] for call in log.call_args_list))

    def test_duplicate_helper_does_not_open_game(self):
        with patch('ultrawide.ui.kernel32.CreateMutexW', return_value=1), \
             patch('ultrawide.ui.kernel32.CloseHandle'), \
             patch('ultrawide.ui.ctypes.get_last_error', return_value=183), \
             patch('ultrawide.ui.Game') as factory:
            run_helper()
        factory.assert_not_called()

    def test_invalid_engine_size_stops_before_mutation(self):
        import sc2_ultrawide as cli
        game = unittest.mock.Mock()
        game.addr = SimpleNamespace(display_mode=1, stored_width=2, stored_height=3)
        game.read_u32.side_effect = [1, 0, 0]
        with patch.object(sys, 'argv', ['sc2_ultrawide.py', 'apply', '--no-helper']), \
             patch.object(cli, 'Game', return_value=game), \
             patch.object(cli, 'find_addresses'), \
             patch.object(cli, 'console_plan', return_value=None), \
             patch.object(cli, 'window_size', return_value=(2561, 1440)), \
             patch.object(cli, 'monitor_rect', return_value=(0, 0, 3440, 1440)), \
             patch.object(cli, 'set_window') as resize, \
             patch.object(cli, 'fix_console') as hud, \
             patch.object(cli, 'start_helper') as helper:
            with self.assertRaisesRegex(SystemExit, 'Nothing was written'):
                cli.main()
        resize.assert_not_called()
        hud.assert_not_called()
        helper.assert_not_called()
        game.close.assert_called_once()

    def test_no_helper_mode_fits_once_without_resizing_existing_wide_window(self):
        import sc2_ultrawide as cli
        game = unittest.mock.Mock()
        game.addr = SimpleNamespace(display_mode=1, stored_width=2, stored_height=3)
        game.read_u32.side_effect = [1, 3440, 1440]
        with patch.object(sys, 'argv', ['sc2_ultrawide.py', 'apply', '--no-helper']), \
             patch.object(cli, 'Game', return_value=game), \
             patch.object(cli, 'find_addresses'), \
             patch.object(cli, 'monitor_rect', return_value=(0, 0, 3440, 1440)), \
             patch.object(cli, 'window_size', return_value=(3440, 1440)), \
             patch.object(cli, 'console_plan', return_value=None), \
             patch.object(cli, 'set_window') as resize, \
             patch.object(cli, 'fix_console', return_value=(0.06, 2.2)) as hud, \
             patch.object(cli, 'start_helper') as helper:
            cli.main()
        resize.assert_not_called()
        hud.assert_called_once_with(game, game.window.return_value)
        helper.assert_not_called()

    def test_validated_full_monitor_hud_can_be_refit_with_zero_resize_fields(self):
        import sc2_ultrawide as cli
        game = unittest.mock.Mock()
        game.addr = SimpleNamespace(display_mode=1, stored_width=2, stored_height=3)
        game.read_u32.side_effect = [1, 0, 0]
        with patch.object(sys, 'argv', ['sc2_ultrawide.py', 'apply', '--no-helper']), \
             patch.object(cli, 'Game', return_value=game), \
             patch.object(cli, 'find_addresses'), \
             patch.object(cli, 'monitor_rect', return_value=(0, 0, 3440, 1440)), \
             patch.object(cli, 'window_size', return_value=(3440, 1440)), \
             patch.object(cli, 'is_wide', return_value=True), \
             patch.object(cli, 'console_plan', return_value=(100, 200, (0, 1), (0.06, 2.2))), \
             patch.object(cli, 'set_window') as resize, \
             patch.object(cli, 'fix_console', return_value=(0.06, 2.2)) as hud, \
             patch.object(cli, 'start_helper') as helper:
            cli.main()
        resize.assert_not_called()
        hud.assert_called_once()
        helper.assert_not_called()

    def test_restore_attempts_all_flags_after_failure(self):
        writes = []
        def write(rva, data):
            writes.append((rva, data))
            if len(writes) == 1 or rva == 200:
                raise OSError('injected write failure')
        game = SimpleNamespace(addr=SimpleNamespace(listen_value=100, render_value=200),
                               read=lambda rva, size: b'\0' * size, write=write)
        with patch('ultrawide.game.monitor_rect', return_value=(0, 0, 3440, 1440)):
            with self.assertRaisesRegex(RuntimeError, 'restore all temporary flags'):
                set_window(game, 1, 3440, 1440, bypass=True)
        self.assertEqual([address for address, _ in writes], [108, 200, 108, 100])

    def test_hud_writer_uses_same_pid(self):
        game = SimpleNamespace(pid=12345)
        writer = unittest.mock.Mock()
        writer.read_abs.return_value = b'\0'
        with patch('ultrawide.ui.console_plan', return_value=(1000, 2000, (0, 1), (0.06, 2.2))), \
             patch('ultrawide.ui.Game', return_value=writer) as factory:
            fix_console(game, 1)
        factory.assert_called_once_with(write=True, pid=12345)
        writer.close.assert_called_once()

    def constructor(self, insertion=b'', target=0x2000, method=0x1010):
        code = bytearray(b'\xcc' * 128)
        load = b'\x48\x8d\x05' + struct.pack('<i', target - (0x1000 + 32 + 7))
        body = load + insertion + b'\x48\x89\x03'
        body += b'\xc7\x43\x34\x00\x00\x80\x3f\xc7\x43\x38\x00\x00\x80\x3f'
        code[32:32 + len(body)] = body
        return 0x1000, bytes(code), 0x2000, struct.pack('<3Q', *([method] * 3)), 0

    def test_original_constructor(self):
        self.assertEqual(find_model_entry_vtable(*self.constructor()), 0x2000)

    def test_98370_constructor(self):
        insertion = b'\x48\xc7\x05\x01\x02\x03\x04\x10\x00\x00\x00'
        self.assertEqual(find_model_entry_vtable(*self.constructor(insertion)), 0x2000)

    def test_rejects_unknown_constructor_insertion(self):
        with self.assertRaises(LookupError):
            find_model_entry_vtable(*self.constructor(b'\x90'))

    def test_rejects_non_vtable_targets(self):
        for kwargs in ({'target': 0x3000}, {'method': 0x5000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(LookupError):
                find_model_entry_vtable(*self.constructor(**kwargs))

    def test_rejects_false_cap(self):
        for value in (-1.0, float('nan'), float('inf'), 0.0, 2.4):
            with self.subTest(value=value):
                self.assertEqual(locate_cap(FakeMemory(value)), (None, {}))

    def test_accepts_expected_cap(self):
        target, sites = locate_cap(FakeMemory(1366 / 768))
        self.assertEqual(target, 0x2000)
        self.assertEqual(list(sites), ['resolution list filter'])

    def test_rejects_ambiguous_template(self):
        game = FakeMemory(1366 / 768)
        game.code *= 2
        self.assertEqual(locate_cap(game), (None, {}))



if __name__ == '__main__':
    unittest.main(verbosity=2)
