"""Native build-98370 life-bar callbacks. No game access at import time.

Allocated code only; existing executable pages are never patched. Capture checks
both native capacities; only a recorded life-bar vertex range is transformed.
"""
import struct

class Assembler:
    def __init__(self):self.code=bytearray();self.labels={};self.fixups=[]
    def emit(self,s):self.code.extend(bytes.fromhex(s))
    def imm(self,v):self.code.extend(struct.pack('<Q',v))
    def label(self,n):self.labels[n]=len(self.code)
    def jump(self,op,n):
        self.emit(op);self.fixups.append((len(self.code),n));self.code.extend(b'\0'*4)
    def finish(self):
        for at,n in self.fixups:struct.pack_into('<i',self.code,at,self.labels[n]-at-4)
        return bytes(self.code)

def build_callback(state, extended=False):
    a=Assembler();e=a.emit;j=a.jump;L=a.label
    e('49 BB');a.imm(state)
    e('31 D2 49 3B 4B 18');j('0F 84','unmap')
    if extended:
        e('48 8B 01 49 3B 83 50 01 00 00');j('0F 85','type_ready')
        e('BA 02 00 00 00')
        L('type_ready')
    e('41 83 3B 00');j('0F 84','tail')
    e('48 8B 04 24')
    if extended:
        e('83 FA 02');j('0F 85','simple_site')
        e('49 3B 83 40 01 00 00');j('0F 85','tail');j('E9','site_ready')
        L('simple_site')
    e('49 3B 43 08');j('0F 85','tail')
    if extended:L('site_ready')
    e('41 83 7B 04 00');j('0F 84','capture')
    e('49 3B 5B 28');j('0F 85','skip')
    j('E9','finish')
    L('unmap');e('BA 01 00 00 00')
    e('41 83 3B 00');j('0F 84','tail')
    e('41 83 7B 04 00');j('0F 84','tail')
    L('finish')
    e('65 48 8B 04 25 48 00 00 00 49 3B 43 40');j('0F 85','skip')
    e('4D 8B 43 30 41 8B 00 45 8B 4B 20 44 29 C8 3D 00 00 01 00');j('0F 87','skip')
    e('41 3B 43 14');j('0F 82','skip')
    e('41 8B 43 14')                       # exact recorded range, not all following vertices
    e('41 89 43 24 41 89 C0 4D 8B 53 28 4D 6B C9 18 4D 01 CA')
    e('F3 41 0F 10 53 3C F3 41 0F 10 1A 0F 28 E3')
    L('bounds');e('F3 41 0F 10 02 F3 0F 5D D8 F3 0F 5F E0')
    e('49 83 C2 18 41 FF C8');j('0F 85','bounds')
    e('F3 41 0F 11 5B 60 F3 41 0F 11 63 64')
    e('0F 28 CB F3 0F 58 CC B8 00 00 00 3F 66 0F 6E E8 F3 0F 59 CD F3 41 0F 11 4B 38')
    e('45 8B 43 24 4D 8B 53 28 45 8B 4B 20 4D 6B C9 18 4D 01 CA')
    e('4D 8D 8B 00 08 00 00')
    L('transform')
    e('F3 41 0F 6F 2A F3 41 0F 7F 29 49 8B 42 10 49 89 41 10')
    e('F3 41 0F 10 02 F3 0F 5C C1 F3 0F 59 C2 F3 0F 58 C1 F3 41 0F 11 02')
    e('F3 41 0F 6F 2A F3 41 0F 7F A9 00 20 00 00 49 8B 42 10 49 89 81 10 20 00 00')
    e('49 83 C2 18 49 83 C1 18 41 FF C8');j('0F 85','transform')
    e('F3 0F 5C D9 F3 0F 59 DA F3 0F 58 D9 F3 41 0F 11 5B 68')
    e('F3 0F 5C E1 F3 0F 59 E2 F3 0F 58 E1 F3 41 0F 11 63 6C')
    # Completion ring: tag, vertices, four bounds, fraction, cells.
    e('49 8B 83 00 01 00 00 83 E0 1F 48 C1 E0 06 4D 8D 8C 03 00 60 00 00')
    e('41 8B 83 F0 00 00 00 41 89 01 41 8B 43 24 41 89 41 04')
    e('F2 41 0F 10 6B 60 F2 41 0F 11 69 08 F2 41 0F 10 6B 68 F2 41 0F 11 69 10')
    e('41 8B 83 F4 00 00 00 41 89 41 18 41 8B 83 FC 00 00 00 41 89 41 1C')
    for source,dest in ((0x86C,32),(0x286C,36),(0x804,40),(0x81C,44),(0x2804,48),(0x281C,52)):
        e('41 8B 83');a.code.extend(struct.pack('<I',source));e('41 89 41');a.code.append(dest)
    e('49 FF 83 00 01 00 00 49 FF 43 48');j('E9','clear')
    L('skip');e('49 FF 43 50')
    L('clear');e('41 C7 43 04 00 00 00 00 83 FA 01');j('0F 84','tail')
    L('capture')
    e('41 83 7B 10 00');j('0F 84','selected')
    if extended:e('48 8B 01 8B 40 F0 49 89 CA 49 29 C2 41 8B 02')
    else:e('8B 81 50 FD FF FF')
    e('41 3B 43 10');j('0F 85','tail')
    L('selected')
    e('81 79 50 00 00 80 3F');j('0F 87','tail')  # finite fraction in [0,1]
    e('80 79 5C 01');j('0F 85','tail')
    e('8B 41 58 83 F8 01');j('0F 82','tail')
    if extended:
        e('83 FA 02');j('0F 85','simple_capacity')
        e('83 F8 1F');j('0F 87','tail')
        e('44 8D 14 C5 04 00 00 00 4C 8B 84 24 78 01 00 00 41 8B 00')
        e('3D 00 00 01 00');j('0F 83','tail')
        e('41 89 C1 45 01 D1 44 3B 8C 24 68 01 00 00');j('0F 87','tail')
        e('4C 8B 8C 24 80 01 00 00 41 8B 01 3D 00 00 02 00');j('0F 83','tail')
        j('E9','index_count')
        L('simple_capacity')
    e('83 F8 3F');j('0F 87','tail')
    e('44 8D 14 85 04 00 00 00')             # expected = 4*(cells+1)
    e('4C 8B 84 24 68 01 00 00 41 8B 00 3D 00 00 01 00');j('0F 83','tail')
    e('41 89 C1 45 01 D1 44 3B 8C 24 58 01 00 00');j('0F 87','tail')
    e('4C 8B 8C 24 70 01 00 00 41 8B 01 3D 00 00 02 00');j('0F 83','tail')
    if extended:L('index_count')
    e('45 89 D1 41 C1 E9 02 47 8D 0C 49 41 D1 E1 44 01 C8')
    if extended:
        e('83 FA 02');j('0F 85','simple_index_capacity')
        e('3B 84 24 70 01 00 00');j('0F 87','tail');j('E9','capacities_ready')
        L('simple_index_capacity')
    e('3B 84 24 60 01 00 00');j('0F 87','tail')
    if extended:L('capacities_ready')
    e('45 89 53 14 41 8B 00 41 89 43 20 49 89 5B 28 4D 89 43 30')
    if extended:
        e('48 8B 01 8B 40 F0 49 89 CA 49 29 C2 41 8B 02')
    else:e('8B 81 50 FD FF FF')
    e('41 89 83 F0 00 00 00')
    e('8B 41 50 41 89 83 F4 00 00 00 8B 41 58 41 89 83 FC 00 00 00')
    e('65 48 8B 04 25 48 00 00 00 49 89 43 40 41 C7 43 04 01 00 00 00')
    L('tail');e('83 FA 01');j('0F 84','tail_unmap')
    if extended:
        e('83 FA 02');j('0F 85','tail_simple')
        e('49 8B 83 48 01 00 00 FF E0')
        L('tail_simple')
    e('49 8B 83 80 00 00 00 FF E0')
    L('tail_unmap');e('49 8B 83 88 00 00 00 FF E0')
    return a.finish()

def build_map_callback(state, lease=False, extended=False):
    """Preserve mapping arguments; restrict installation by three return addresses."""
    a=Assembler();e=a.emit;j=a.jump;L=a.label
    e('49 BB');a.imm(state)
    e('41 83 3B 00');j('0F 84','map_tail')
    e('48 8B 04 24 49 3B 83 98 00 00 00');j('0F 85','map_tail')
    e('48 8B 44 24 40 49 3B 83 A0 00 00 00');j('0F 85','map_tail')
    e('48 8B 44 24 70 49 3B 83 A8 00 00 00');j('0F 85','map_tail')
    if lease:
        e('49 3B 4B 18');j('0F 85','map_tail')
        e('49 8B 83 E0 00 00 00 49 3B 83 10 01 00 00');j('0F 86','lease_valid')
        e('41 C7 03 00 00 00 00 41 C7 43 04 00 00 00 00')
        e('49 8B 83 18 01 00 00 48 89 01 49 FF 83 20 01 00 00');j('E9','map_tail')
        L('lease_valid')
        e('41 C7 43 04 00 00 00 00')       # reset stale pending state at new frame
    e('52 41 50 41 51')                      # preserve RDX/R8/R9
    e('49 8B 83 B8 00 00 00 8B 00 85 C0');j('0F 84','map_pop')
    e('3D');a.code.extend(struct.pack('<I',4096 if extended else 128));j('0F 87','map_pop')
    e('4D 8B 93 B0 00 00 00 4D 8B 12 4D 85 D2');j('0F 84','map_pop')
    e('4D 89 93 D0 00 00 00 41 89 83 D8 00 00 00')
    e('49 FF 83 E0 00 00 00')
    e('4D 8B 83 C0 00 00 00 4D 8B 8B C8 00 00 00')
    L('map_loop')
    e('4D 39 82 B0 02 00 00');j('0F 85','map_next')
    e('4D 89 8A B0 02 00 00 49 FF 83 E8 00 00 00')
    L('map_next')
    if extended:
        for offset,field in ((0x190,0x160),(0x1F0,0x168),(0x250,0x170)):
            e('4D 8B 8B');a.code.extend(struct.pack('<I',field))
            e('4D 39 82');a.code.extend(struct.pack('<I',offset));j('0F 85',f'next_{offset}')
            e('4D 89 8A');a.code.extend(struct.pack('<I',offset));e('49 FF 83 E8 00 00 00')
            L(f'next_{offset}')
        e('4D 8B 83 58 01 00 00 4D 8B 8B 50 01 00 00')
        e('4D 39 82 10 03 00 00');j('0F 85','next_composite')
        e('4D 89 8A 10 03 00 00 49 FF 83 E8 00 00 00')
        L('next_composite');e('4D 8B 83 C0 00 00 00 4D 8B 8B C8 00 00 00')
    e('49 81 C2 C0 03 00 00 FF C8');j('0F 85','map_loop')
    L('map_pop');e('41 59 41 58 5A')
    L('map_tail');e('49 8B 83 90 00 00 00 FF E0')
    return a.finish()
