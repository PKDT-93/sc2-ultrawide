"""Offline lifecycle checks. No game process is opened or patched by these tests."""
import ctypes
import struct
import unittest
from unittest.mock import Mock,patch
from ultrawide import healthbars as hb


class HealthBarLifecycleTests(unittest.TestCase):
    def session(self):
        h=hb.HealthBarSession()
        h.game=Mock(base=0x10000000,handle=1)
        h.mem=0x10000;h.exposed=True
        p=h.probe=Mock(arrptr=0x100,countptr=0x108,rendererptr=0x110,renderer=0x200,pool=0x300)
        values={p.arrptr:0x4000,p.rendererptr:p.renderer,p.renderer+0xBE0:p.pool,p.pool:h.mem+0x600}
        for offset,clone in ((0x190,0x400),(0x1F0,0x440),(0x250,0x480),(0x2B0,0x4C0),(0x310,0x500)):
            values[0x4000+offset]=h.mem+clone
        p.u64.side_effect=lambda address:values[address]
        p.u32.return_value=1
        state=bytearray(256);struct.pack_into('<I',state,0,1)
        struct.pack_into('<Q',state,224,123);struct.pack_into('<Q',state,72,456)
        h.game.read_abs.return_value=bytes(state)
        def write(address,data):
            if len(data)==8:values[address]=struct.unpack('<Q',data)[0]
        h.game.write_abs.side_effect=write
        return h,values

    def test_construct_has_no_game_access(self):
        with patch.object(hb,'Game') as game:
            h=hb.HealthBarSession();h.close()
            game.assert_not_called()

    def test_duplicate_session_rejected_before_game_open(self):
        mutex=hb.kernel32.CreateMutexW(None,False,hb.MUTEX)
        try:
            with patch.object(hb,'Game') as game:
                with self.assertRaisesRegex(RuntimeError,'另一窗口'):
                    hb.HealthBarSession().start()
                game.assert_not_called()
        finally:hb.kernel32.CloseHandle(mutex)

    def test_heartbeat_renews_from_actual_render_frame(self):
        h,_=self.session()
        with patch.object(h,'alive',return_value=True),patch.object(hb,'window_size',return_value=(3440,1440)):
            stats=h.poll()
        self.assertEqual(stats['bars'],456)
        h.game.write_abs.assert_called_once_with(h.mem+0x110,struct.pack('<Q',723))

    def test_changed_graphics_pool_refuses_heartbeat(self):
        h,values=self.session();values[h.probe.renderer+0xBE0]=0x9999
        with patch.object(h,'alive',return_value=True),patch.object(hb,'window_size',return_value=(3440,1440)):
            with self.assertRaisesRegex(RuntimeError,'图形缓冲区'):h.poll()
        h.game.write_abs.assert_not_called()

    def test_window_resize_refuses_heartbeat(self):
        h,_=self.session()
        with patch.object(h,'alive',return_value=True),patch.object(hb,'window_size',return_value=(2560,1440)):
            with self.assertRaisesRegex(RuntimeError,'3440'):h.poll()
        h.game.write_abs.assert_not_called()

    def test_stop_disables_before_restoring_all_five_types(self):
        h,values=self.session();game=h.game
        with patch.object(h,'alive',return_value=True),patch.object(hb.time,'sleep'):
            h.close()
        first=game.write_abs.call_args_list[0]
        self.assertEqual(first.args,(0x10000,struct.pack('<I',0)))
        self.assertEqual(values[0x300],game.base+hb.POOL_VT)
        for off in (0x190,0x1F0,0x250,0x2B0,0x310):
            self.assertEqual(values[0x4000+off],game.base+hb.VT+(0x10 if off==0x310 else 0))
        game.close.assert_called_once()
        self.assertIsNone(h.game)

    def test_exited_game_never_receives_cleanup_writes(self):
        h,_=self.session();game=h.game
        with patch.object(h,'alive',return_value=False):h.close()
        game.write_abs.assert_not_called();game.close.assert_called_once()

    def test_context_scan_error_still_restores_pool(self):
        h,values=self.session();game=h.game
        h.probe.u32.side_effect=OSError('context read failed')
        with patch.object(h,'alive',return_value=True),patch.object(hb.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'context read failed'):h.close()
        self.assertEqual(values[0x300],game.base+hb.POOL_VT)
        self.assertEqual(game.write_abs.call_args_list[0].args,(0x10000,struct.pack('<I',0)))

    def test_unpublished_allocation_freed_without_callback_writes(self):
        h,_=self.session();h.exposed=False;game=h.game
        with patch.object(h,'alive',return_value=True),patch.object(hb.kernel32,'VirtualFreeEx',return_value=1) as free:
            h.close()
        free.assert_called_once_with(1,0x10000,0,0x8000)
        game.write_abs.assert_not_called()


if __name__=='__main__':unittest.main()
