"""Isolated native callback tests: allocate executable memory only in test children.

Never opens StarCraft II. No Capstone or third-party test dependencies required.
"""
import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from ultrawide.healthbar_native import Assembler, build_callback, build_map_callback

ALLOCATION_SIZE=0x10000
CODE_OFFSET=0xF000
RING_OFFSET=0x6000
RECORD_SIZE=64



def build_vertex_harness(shim, composite=False):
    a=Assembler();a.emit('53 48 81 EC 80 01 00 00 48 89 D3')
    for prefix,off in (('4C 89 84 24',0x160),('4C 89 8C 24',0x168)):
        a.emit(prefix);a.code.extend(struct.pack('<I',off+(16 if composite else 0)))
    for off,value in ((0x150,0x10000),(0x158,0x20000)):
        a.emit('C7 84 24');a.code.extend(struct.pack('<II',off+(16 if composite else 0),value))
    a.emit('48 B8');a.imm(shim);a.emit('FF D0');ret=len(a.code)
    a.emit('48 81 C4 80 01 00 00 5B C3')
    return a.finish(),ret

def vertex_self_test():
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.VirtualAlloc.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.c_uint]
    k.VirtualAlloc.restype=ctypes.c_void_p
    k.VirtualProtect.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.POINTER(ctypes.c_uint)]
    k.VirtualFree.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint]
    mem=k.VirtualAlloc(None,ALLOCATION_SIZE,0x3000,4)
    if not mem:raise ctypes.WinError(ctypes.get_last_error())
    try:
        shim=mem+CODE_OFFSET;harness=shim+0x800;harness2=shim+0x900
        count=shim+0xA00;count2=count+64;unmap=count+128;pool=mem+0x700
        code=build_callback(mem,extended=True);assert len(code)<0x800
        ctypes.memmove(shim,code,len(code))
        hc,ro=build_vertex_harness(shim);ctypes.memmove(harness,hc,len(hc))
        hc2,ro2=build_vertex_harness(shim,composite=True);ctypes.memmove(harness2,hc2,len(hc2))
        stub=bytes.fromhex('80795c007506b80c000000c38b41588d048504000000c3')
        ctypes.memmove(count,stub,len(stub));ctypes.memmove(unmap,bytes.fromhex('b87b000000c3'),6)
        stub2=bytes.fromhex('80795c007506b81c000000c38b41588d04c504000000c3')
        ctypes.memmove(count2,stub2,len(stub2))
        old=ctypes.c_uint();assert k.VirtualProtect(shim,4096,0x20,ctypes.byref(old))
        fn=ctypes.WINFUNCTYPE(ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p,
                             ctypes.c_void_p,ctypes.c_void_p)(harness)
        fn2=ctypes.WINFUNCTYPE(ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p,
                              ctypes.c_void_p,ctypes.c_void_p)(harness2)
        cells=[4,4,5,4,4,63,10]
        offsets=[0x190,0x1F0,0x250,0x2B0,0x2B0,0x2B0,0x310]
        clones={off:mem+0x400+i*0x40 for i,off in enumerate((0x190,0x1F0,0x250,0x2B0,0x310))}
        contexts=ctypes.create_string_buffer(len(cells)*0x3C0)
        bars=[];tags=[]
        for i,n in enumerate(cells):
            p=ctypes.addressof(contexts)+i*0x3C0;off=offsets[i]
            bars.append(p+off);tags.append(0xA000000+i+1)
            struct.pack_into('<I',contexts,i*0x3C0,tags[-1])
            struct.pack_into('<Q',contexts,i*0x3C0+off,clones[off])
            struct.pack_into('<fI',contexts,i*0x3C0+off+0x50,0.2+i*0.1,0)
            struct.pack_into('<I',contexts,i*0x3C0+off+0x58,n);contexts[i*0x3C0+off+0x5C]=1
        desc=bytes(contexts)
        vertices=ctypes.create_string_buffer(1024*24);vbase=ctypes.addressof(vertices)
        for i in range(1024):struct.pack_into('<3fI2f',vertices,i*24,100+(i%7)*23,75+i%4,0.5,0xFF000000+i*12345,0.25,0.75)
        original=bytes(vertices);counter=ctypes.c_uint(3);indices=ctypes.c_uint(6);ratio=2560/3440
        def reset(tag=0):
            ctypes.memset(mem,0,CODE_OFFSET);ctypes.memmove(vbase,original,len(original))
            struct.pack_into('<IIQIIQ',b:=bytearray(256),0,1,0,harness+ro,tag,0,pool)
            struct.pack_into('<f',b,60,ratio);struct.pack_into('<QQ',b,128,count,unmap)
            ctypes.memmove(mem,bytes(b),len(b));counter.value=3;indices.value=6
            for off,vt in clones.items():ctypes.c_uint.from_address(vt-16).value=off
            ctypes.c_ulonglong.from_address(mem+0x140).value=harness2+ro2
            ctypes.c_ulonglong.from_address(mem+0x148).value=count2
            ctypes.c_ulonglong.from_address(mem+0x150).value=clones[0x310]
        def call(bar):
            selected=fn2 if bar==bars[-1] else fn
            return selected(bar,vbase,ctypes.byref(counter),ctypes.byref(indices))
        reset();ranges=[]
        for i,(bar,n) in enumerate(zip(bars,cells)):
            vertices_needed=(8 if i==len(cells)-1 else 4)*n+4
            assert call(bar)==vertices_needed
            ranges.append((counter.value,counter.value+vertices_needed))
            counter.value+=vertices_needed;indices.value+=vertices_needed//4*6
        assert call(pool)==123
        result=bytes(vertices)
        for start,end in ranges:
            xs=[struct.unpack_from('<f',original,i*24)[0] for i in range(start,end)]
            center=(min(xs)+max(xs))/2
            for i,x in zip(range(start,end),xs):
                got=struct.unpack_from('<f',result,i*24)[0]
                assert abs(got-(center+(x-center)*ratio))<0.001
                assert result[i*24+4:i*24+24]==original[i*24+4:i*24+24]
        assert result[:3*24]==original[:3*24]
        assert result[ranges[-1][1]*24:]==original[ranges[-1][1]*24:]
        assert bytes(contexts)==desc
        assert ctypes.c_ulonglong.from_address(mem+72).value==len(cells)
        records=[struct.unpack('<II5fI',ctypes.string_at(mem+RING_OFFSET+i*RECORD_SIZE,32)) for i in range(len(cells))]
        assert [r[0] for r in records]==tags
        assert [r[-1] for r in records]==cells
        for i in range(len(cells)):
            more=struct.unpack('<6I',ctypes.string_at(mem+RING_OFFSET+i*RECORD_SIZE+32,24))
            assert more[0]==more[1] and more[2:4]==more[4:6]
        reset(tags[2]);call(bars[0]);counter.value+=20;indices.value+=30
        call(bars[2]);counter.value+=24;indices.value+=36;call(pool)
        assert ctypes.c_ulonglong.from_address(mem+72).value==1
        reset();call(bars[0]);counter.value+=24;call(pool)
        assert bytes(vertices)[:3*24]==original[:3*24]
        assert bytes(vertices)[23*24:]==original[23*24:]
        assert ctypes.c_ulonglong.from_address(mem+72).value==1
        reset();call(bars[0]);counter.value=2;call(pool)
        assert bytes(vertices)==original and ctypes.c_ulonglong.from_address(mem+80).value==1
        reset();counter.value=65530;call(bars[0]);assert ctypes.c_uint.from_address(mem+4).value==0
        reset();indices.value=131070;call(bars[0]);assert ctypes.c_uint.from_address(mem+4).value==0
        reset();ctypes.c_uint.from_address(bars[0]+0x58).value=64;call(bars[0])
        assert ctypes.c_uint.from_address(mem+4).value==0
        reset();ctypes.c_uint.from_address(mem).value=0;call(bars[1]);counter.value+=20;call(pool)
        assert bytes(vertices)==original
        assert call(bars[-1])==84
        print(json.dumps(dict(native_all_life_vertex_self_test='PASS',callback_bytes=len(code),checks=[
            'seven consecutive bars, including two-row composite','mixed 4/5/10/63 cells',
            'four ordinary context offsets and composite context offset',
            'last bar finished at buffer unmap','original composite count forwarded',
            'each bar transformed about its own center','Y/Z/color/UV unchanged',
            'bar descriptors unchanged','unrelated vertices unchanged',
            'target filter','completion ring tags and cell counts',
            'following unrelated geometry left intact','decreasing vertex counter rejected','vertex and index capacity guards',
            'more than 63 cells rejected','disabled pass-through']),indent=2))
    finally:k.VirtualFree(mem,0,0x8000)

def build_map_harness(shim, second_cookie, third_cookie):
    a=Assembler();a.emit('48 81 EC 88 00 00 00')
    a.emit('48 B8');a.imm(second_cookie);a.emit('48 89 44 24 38')
    a.emit('48 B8');a.imm(third_cookie);a.emit('48 89 44 24 68')
    a.emit('48 B8');a.imm(shim);a.emit('FF D0');ret=len(a.code)
    a.emit('48 81 C4 88 00 00 00 C3')
    return a.finish(),ret

def map_self_test():
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.VirtualAlloc.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.c_uint]
    k.VirtualAlloc.restype=ctypes.c_void_p
    k.VirtualProtect.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.POINTER(ctypes.c_uint)]
    k.VirtualFree.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint]
    mem=k.VirtualAlloc(None,8192,0x3000,4)
    if not mem:raise ctypes.WinError(ctypes.get_last_error())
    try:
        shim=mem+4096;harness=shim+1024;original=shim+2048
        code=build_map_callback(mem,lease=True,extended=True);ctypes.memmove(shim,code,len(code))
        harness_code,ret_offset=build_map_harness(shim,0x1234,0x5678)
        ctypes.memmove(harness,harness_code,len(harness_code))
        stub=bytes.fromhex('4889c84831d04c31c04c31c8c3')
        ctypes.memmove(original,stub,len(stub))
        old=ctypes.c_uint();assert k.VirtualProtect(shim,4096,0x20,ctypes.byref(old))
        fn=ctypes.WINFUNCTYPE(ctypes.c_ulonglong,ctypes.c_void_p,ctypes.c_ulonglong,
                             ctypes.c_ulonglong,ctypes.c_ulonglong)(harness)
        contexts=ctypes.create_string_buffer(0x780);base=ctypes.addressof(contexts)
        first=base+0x2B0;second=first+0x3C0
        arr=ctypes.c_ulonglong(base);count=ctypes.c_uint(2)
        native_vt=0xABCDEF001;clone_vt=0xABCDEF002;pool=mem+0x380
        def reset():
            ctypes.memset(mem,0,256)
            ctypes.c_uint.from_address(mem).value=1
            ctypes.c_ulonglong.from_address(mem+24).value=pool
            ctypes.c_ulonglong.from_address(mem+0x110).value=600
            ctypes.c_ulonglong.from_address(mem+0x118).value=0x12345678
            ctypes.c_ulonglong.from_address(mem+0x120).value=0
            ctypes.c_ulonglong.from_address(pool).value=0x23456789
            state=struct.pack('<8Q',original,harness+ret_offset,0x1234,0x5678,
                              ctypes.addressof(arr),ctypes.addressof(count),native_vt,clone_vt)
            ctypes.memmove(mem+144,state,len(state))
            ctypes.c_ulonglong.from_address(first).value=native_vt
            ctypes.c_ulonglong.from_address(second).value=native_vt
            for off in (0x160,0x168,0x170):ctypes.c_ulonglong.from_address(mem+off).value=clone_vt
            ctypes.c_ulonglong.from_address(mem+0x158).value=native_vt+100
            ctypes.c_ulonglong.from_address(mem+0x150).value=clone_vt+100
            ctypes.c_ulonglong.from_address(base+0x310).value=native_vt+100
            ctypes.c_ulonglong.from_address(base+0x190).value=native_vt
        def call():assert fn(pool,0x22,0x33,0x44)==pool^0x22^0x33^0x44
        reset();call()
        assert ctypes.c_ulonglong.from_address(first).value==clone_vt
        assert ctypes.c_ulonglong.from_address(second).value==clone_vt
        assert ctypes.c_ulonglong.from_address(mem+224).value==1
        assert ctypes.c_ulonglong.from_address(mem+232).value==4
        assert ctypes.c_ulonglong.from_address(base+0x310).value==clone_vt+100
        assert ctypes.c_ulonglong.from_address(base+0x190).value==clone_vt
        reset();ctypes.c_ulonglong.from_address(mem+224).value=601;call()
        assert ctypes.c_ulonglong.from_address(mem).value==0
        assert ctypes.c_ulonglong.from_address(pool).value==0x12345678
        assert ctypes.c_ulonglong.from_address(mem+0x120).value==1
        assert ctypes.c_ulonglong.from_address(first).value==native_vt
        for bad in (152,160,168):
            reset();ctypes.c_ulonglong.from_address(mem+bad).value=0;call()
            assert ctypes.c_ulonglong.from_address(first).value==native_vt
        reset();count.value=4097;call()
        assert ctypes.c_ulonglong.from_address(first).value==native_vt
        reset();count.value=0;call()
        assert ctypes.c_ulonglong.from_address(first).value==native_vt
        reset();count.value=2;ctypes.c_uint.from_address(mem).value=0;call()
        assert ctypes.c_ulonglong.from_address(first).value==native_vt
        reset();ctypes.c_ulonglong.from_address(first).value=0xAAA;call()
        assert ctypes.c_ulonglong.from_address(first).value==0xAAA
        assert ctypes.c_ulonglong.from_address(second).value==clone_vt
        print(json.dumps(dict(native_map_map_self_test='PASS',shim_bytes=len(code),checks=[
            'per-frame vtable installation','all four mapping arguments preserved',
            'original return value forwarded','three return addresses independently checked',
            'zero and excessive context counts rejected','disabled pass-through',
            'unknown vtable not overwritten','expired helper lease disables callbacks and restores pool']),indent=2))
    finally:k.VirtualFree(mem,0,0x8000)


@unittest.skipUnless(os.name=='nt' and ctypes.sizeof(ctypes.c_void_p)==8,'Windows x64 only')
class NativeCallbackTests(unittest.TestCase):
    def child(self,mode):
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),mode],
                              capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('"PASS"',result.stdout)

    def test_vertex_contract_in_isolated_process(self):self.child('--vertices')
    def test_map_and_lease_contract_in_isolated_process(self):self.child('--mapping')


if __name__=='__main__':
    if sys.argv[1:]==['--vertices']:vertex_self_test()
    elif sys.argv[1:]==['--mapping']:map_self_test()
    else:unittest.main()
