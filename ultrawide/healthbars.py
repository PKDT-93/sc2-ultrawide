"""Opt-in build-98370 segmented life-bar preview, owned by the GUI window.

No access at import. Existing code pages and game files are never modified.
The native map callback has a render-frame heartbeat lease, so a dead controller
eventually disables itself and restores the pool vtable. Allocations remain valid
until game exit, including after stop, to protect in-flight callback references.
"""
import ctypes
import struct
import time
from .game import Game, window_size
from .winapi import kernel32, ERROR_ALREADY_EXISTS
from .healthbar_probe import Probe, VT, POOL_VT, METHODS, require
from .healthbar_native import build_callback, build_map_callback

SIZE=0x10000
CODE=0xF000
MUTEX='Local\\SC2UltrawideHealthBars98370Preview'

kernel32.VirtualAllocEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.c_uint]
kernel32.VirtualAllocEx.restype=ctypes.c_void_p
kernel32.VirtualFreeEx.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint]
kernel32.FlushInstructionCache.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t]
kernel32.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint)]


class HealthBarSession:
    def __init__(self, log=lambda text:None):
        self.log=log
        self.game=None
        self.probe=None
        self.mutex=None
        self.mem=0
        self.exposed=False

    def alive(self):
        code=ctypes.c_uint()
        if not kernel32.GetExitCodeProcess(self.game.handle,ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value==259

    def start(self):
        require(self.game is None,'Session already started')
        ctypes.set_last_error(0)
        self.mutex=kernel32.CreateMutexW(None,False,MUTEX)
        if not self.mutex:raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error()==ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(self.mutex);self.mutex=None
            raise RuntimeError('另一窗口已经启用血条修复，请先在那里停止。')
        try:
            self.game=Game(write=True)
            self.probe=p=Probe(self.game)
            baseline=p.check()
            self.log(f'precheck passed: pid={self.game.pid}, build={self.game.build}, life contexts={len(baseline["contexts"])}')
            self.log(f'allocation pending: {SIZE} bytes; original pool={p.pool:#x}')
            self.mem=kernel32.VirtualAllocEx(self.game.handle,None,SIZE,0x3000,4)
            if not self.mem:raise ctypes.WinError(ctypes.get_last_error())
            self.log(f'allocated: {self.mem:#x}; disabled until installation completes')
            mem=self.mem;g=self.game;blob=bytearray(SIZE)
            struct.pack_into('<IIQIIQ',blob,0,0,0,g.base+0xAEC197,0,0,p.pool)
            struct.pack_into('<f',blob,60,2560/3440)
            struct.pack_into('<10Q',blob,128,g.base+METHODS[0],g.base+0xCDD8D0,
                             g.base+0xCDD850,g.base+0xCACFB5,g.base+0xCA43A4,
                             g.base+0xAF0505,p.arrptr,p.countptr,g.base+VT,mem+0x4C0)
            struct.pack_into('<QQ',blob,0x110,600,g.base+POOL_VT)
            struct.pack_into('<7Q',blob,0x140,g.base+0xAEA3C2,g.base+0xAF1490,
                             mem+0x500,g.base+VT+0x10,mem+0x400,mem+0x440,mem+0x480)
            for offset,clone in ((0x190,0x400),(0x1F0,0x440),(0x250,0x480),(0x2B0,0x4C0),(0x310,0x500)):
                struct.pack_into('<I',blob,clone-16,offset)
                blob[clone-8:clone+48]=g.read(VT+(0x10 if offset==0x310 else 0)-8,56)
                struct.pack_into('<Q',blob,clone,mem+CODE)
            blob[0x5F8:0x700]=g.read(POOL_VT-8,0x108)
            struct.pack_into('<Q',blob,0x648,mem+CODE+0x800)
            struct.pack_into('<Q',blob,0x650,mem+CODE)
            code=build_callback(mem,extended=True);mapcode=build_map_callback(mem,lease=True,extended=True)
            require(len(code)<0x800 and len(mapcode)<0x800,'Native callback size overflow')
            blob[CODE:CODE+len(code)]=code;blob[CODE+0x800:CODE+0x800+len(mapcode)]=mapcode
            g.write_abs(mem,bytes(blob));old=ctypes.c_uint()
            if not kernel32.VirtualProtectEx(g.handle,ctypes.c_void_p(mem+CODE),4096,0x20,ctypes.byref(old)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.FlushInstructionCache(g.handle,mem+CODE,4096):
                raise ctypes.WinError(ctypes.get_last_error())
            require(p.u64(p.rendererptr)==p.renderer and p.u64(p.renderer+0xBE0)==p.pool,
                    'Graphics pool changed during preparation')
            require(p.u64(p.pool)==g.base+POOL_VT,'Another callback is already installed')
            self.log('install pending; renderer and original vtable verified')
            self.exposed=True
            g.write_abs(p.pool,struct.pack('<Q',mem+0x600))
            g.write_abs(mem,struct.pack('<I',1))
            self.log('enabled: 74.4186% X scale, ordinary 1..63 / composite 1..31 cells, GUI owns session')
            return self.statistics()
        except BaseException:
            self.close()
            raise

    def statistics(self):
        b=self.game.read_abs(self.mem,256)
        return dict(enabled=struct.unpack_from('<I',b)[0],
                    frames=struct.unpack_from('<Q',b,224)[0],
                    bars=struct.unpack_from('<Q',b,72)[0],
                    skipped=struct.unpack_from('<Q',b,80)[0])

    def poll(self):
        if not self.alive():return None
        p=self.probe;g=self.game
        require(window_size(g.window())==(3440,1440),'游戏窗口不再是 3440×1440，已停止血条修复。')
        require(p.u64(p.rendererptr)==p.renderer and p.u64(p.renderer+0xBE0)==p.pool,
                '图形缓冲区已更换，已停止；请重新启用血条修复。')
        stats=self.statistics()
        require(stats['enabled']==1 and p.u64(p.pool)==self.mem+0x600,
                '回调已自行停用或发生变化，已停止血条修复。')
        g.write_abs(self.mem+0x110,struct.pack('<Q',stats['frames']+600))
        return stats

    def close(self):
        g=self.game
        try:
            if g and self.mem:
                if not self.alive():
                    self.log('game exited; process allocation reclaimed by Windows')
                elif not self.exposed:
                    if not kernel32.VirtualFreeEx(g.handle,self.mem,0,0x8000):
                        raise ctypes.WinError(ctypes.get_last_error())
                    self.log('unexposed allocation freed')
                else:
                    g.write_abs(self.mem,struct.pack('<I',0))
                    time.sleep(0.15)
                    p=self.probe
                    errors=[]
                    try:
                        arr=p.u64(p.arrptr);count=p.u32(p.countptr)
                        require(count<=4096 and (count==0 or arr),'Cannot safely enumerate life bars during stop')
                        for i in range(count):
                            for offset,clone in ((0x190,0x400),(0x1F0,0x440),(0x250,0x480),(0x2B0,0x4C0),(0x310,0x500)):
                                ptr=arr+i*0x3C0+offset
                                require(p.u64(p.arrptr)==arr,'Render list moved during stop; callback is disabled')
                                if p.u64(ptr)==self.mem+clone:
                                    g.write_abs(ptr,struct.pack('<Q',g.base+VT+(0x10 if offset==0x310 else 0)))
                    except (OSError,RuntimeError) as error:errors.append(str(error))
                    try:
                        if p.u64(p.pool)==self.mem+0x600:
                            g.write_abs(p.pool,struct.pack('<Q',g.base+POOL_VT))
                        require(p.u64(p.pool)==g.base+POOL_VT,'Pool restoration not verified; exit the game')
                    except (OSError,RuntimeError) as error:errors.append(str(error))
                    require(not errors,'; '.join(errors))
                    self.log(f'stopped and pool restored; disabled {SIZE} byte allocation retained until game exit')
        finally:
            if g:g.close()
            if self.mutex:kernel32.CloseHandle(self.mutex)
            self.game=None;self.mutex=None;self.mem=0;self.exposed=False

    def __enter__(self):
        self.start();return self

    def __exit__(self,*_):
        self.close()
