"""Read-only, exact-build guards for the opt-in life-bar preview."""
import struct
from .game import window_size

VT=0x2AFBA28
POOL_VT=0x2B416A8
METHODS=(0xAF1560,0xAEDF00,0xAF1490,0xAEDE30,0xAF14B0,0xAEDE50)

def require(condition, why):
    if not condition:
        raise RuntimeError(why)

class Probe:
    def __init__(self, game):
        self.g = game

    def u32(self, p):
        return struct.unpack('<I', self.g.read_abs(p, 4))[0]

    def u64(self, p):
        return self.g.read_abs_u64(p)

    def rip(self, rva, n, off, prefix):
        code = self.g.read(rva, n)
        require(code.startswith(bytes.fromhex(prefix)), f'Instruction mismatch at {rva:X}')
        return self.g.base+rva+n+struct.unpack_from('<i', code, off)[0]

    def contexts(self):
        arr = self.u64(self.arrptr)
        count = self.u32(self.countptr)
        require(arr and 0 < count <= 4096, f'No bounded visible bar list: {count}')
        data = self.g.read_abs(arr, count*0x3C0)
        require(len(data) == count*0x3C0, 'Short context read')
        require(self.u64(self.arrptr) == arr and self.u32(self.countptr) == count,
                'Context list changed during read')
        out = []
        for i in range(count):
            off = i*0x3C0
            b = data[off:off+0x3C0]
            bar = off+0x2B0
            width, height, yoff = struct.unpack_from('<iii', data, bar+0x3C)
            out.append(dict(index=i, context=arr+off, tag=struct.unpack_from('<I', b)[0],
                            vtable=struct.unpack_from('<Q', data, bar)[0],
                            flags=struct.unpack_from('<I', b, 0x14)[0],
                            active=data[bar+0x48], width=width, height=height, offset=yoff,
                            fraction=struct.unpack_from('<f', data, bar+0x50)[0],
                            segments=struct.unpack_from('<I', data, bar+0x58)[0],
                            segmented=data[bar+0x5C],
                            composite=dict(vtable=struct.unpack_from('<Q',b,0x310)[0],
                                           active=b[0x358],fraction=struct.unpack_from('<f',b,0x360)[0],
                                           segments=struct.unpack_from('<I',b,0x368)[0],segmented=b[0x36C])))
        return arr, out

    def check(self):
        g = self.g
        require(g.build == '98370', 'Only build 98370 was analysed')
        require(window_size(g.window()) == (3440, 1440), 'Expected 3440x1440 window')
        self.arrptr = self.rip(0xAEF827, 7, 3, '4c0335')
        self.countptr = self.rip(0xAEF791, 6, 2, '8b05')
        self.registry = self.rip(0x1CEF2C2, 7, 3, '4c8d0d')
        self.rendererptr = self.rip(0xAF04E3, 7, 3, '488b0d')
        require(g.read(0xAEC195, 10) == bytes.fromhex('ff104c8bb42460010000'),
                'Vertex count call/stack layout mismatch')
        require(g.read(0xAEA3C0, 10) == bytes.fromhex('ff104c8bb42470010000'),
                'Composite count call/stack layout mismatch')
        require(g.read(0xAF1560, 0x17) == bytes.fromhex(
            '80795c007506b80c000000c38b41588d048504000000c3'), 'Count method mismatch')
        require(g.read(0xCDD8D0, 13) == bytes.fromhex('40534883ec3080793800488bd9'),
                'Pool unmap method mismatch')
        require(g.read(0xCACFB2, 6) == bytes.fromhex('ff5048488903'), 'Pool mapping call mismatch')
        require(g.read(0xCA4380, 6) == bytes.fromhex('40534883ec20'), 'Map wrapper stack mismatch')
        require(g.read(0xCACF50, 6) == bytes.fromhex('40534883ec30'), 'Pool wrapper stack mismatch')
        require(g.read(0xCA439F, 5) == b'\xe8'+struct.pack('<i', 0xCACF50-0xCA43A4),
                'Pool wrapper call mismatch')
        require(g.read(0xAF0500, 5) == b'\xe8'+struct.pack('<i', 0xCA4380-0xAF0505),
                'Health renderer mapping call mismatch')
        require(struct.unpack('<6Q', g.read(VT, 48)) == tuple(g.base+r for r in METHODS),
                'Bar vtable mismatch')
        self.renderer = self.u64(self.rendererptr)
        self.pool = self.u64(self.renderer+0xBE0)
        require(self.u64(self.pool) == g.base+POOL_VT, 'Pool vtable mismatch')
        require(self.u64(g.base+POOL_VT+0x48) == g.base+0xCDD850, 'Pool map mismatch')
        require(self.u64(g.base+POOL_VT+0x50) == g.base+0xCDD8D0, 'Pool unmap mismatch')
        arr, contexts = self.contexts()
        require(all(c['vtable']==g.base+VT for c in contexts),'A life-bar vtable is already changed')
        require(all(c['composite']['vtable']==g.base+VT+0x10 for c in contexts),'Composite vtable mismatch')
        require(any((c['active'] and c['segmented']==1 and 1<=c['segments']<=63
                     and 0<=c['fraction']<=1)
                    or (c['composite']['active'] and c['composite']['segmented']==1
                        and 1<=c['composite']['segments']<=31) for c in contexts),
                'No supported segmented status bar visible')
        return dict(pid=g.pid,build=g.build,base=g.base,path=g.path,array=arr,
                    pool=self.pool,renderer=self.renderer,contexts=contexts,ratio=2560/3440)
