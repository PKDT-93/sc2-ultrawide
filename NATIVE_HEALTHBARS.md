# Build 98370 native status-bar preview: review notes

The feature is opt-in and independent of window/HUD apply. It currently requires the
China x64 build 98370, a 3440×1440 window, and visible supported segmented status bars
when starting. It is intended for user-owned single-player campaign experiments.
No account-safety claim is made.

## Known incomplete rendering

Transport cargo/loading slots remain incorrectly proportioned. The user reported
the slots below the Warp Prism's bars. They are not repaired by this implementation.
Unsegmented fallback drawing and unknown paths are also left unchanged. Do not describe
this as a complete overhead-UI or all-unit rendering fix.

## Files and opt-in path

- `healthbar_probe.py` verifies the exact loaded build, window, original instructions,
  method pointers, renderer/pool and visible context structures before installation.
- `healthbar_native.py` emits x64 machine code using a label/fixup assembler. Importing
  it only defines Python objects; it does not execute or allocate native code.
- `healthbars.py` owns a per-session mutex and a process handle, allocates 64 KiB,
  installs the callbacks, renews their lease and restores pointers on stop.
- The GUI starts an owned worker only after the dedicated enable button. Normal GUI
  startup, CLI `status`, and the existing HUD helper do not enable native callbacks.

The ordinary CLI remains the original window/HUD workflow. The new native behavior
does not silently become part of `apply`.

## Geometry and original call sites

The observed distortion scales horizontal cell bodies, margins and gaps. Changing
catalogue widths changed cell counts, while distance-only compensation left the gaps
and borders too wide. The selected correction instead transforms the completed geometry:

```
center = (min_x + max_x) / 2
x = center + (x - center) * (2560 / 3440)
```

The vertex stride is 24 bytes: three floats XYZ, a 32-bit color, and two float UVs.
Only the first float is written. The midpoint is inferred from completed vertices so
the engine's optional projection factor is already represented in the coordinates.

All addresses below are RVAs within the validated 98370 module, not absolute addresses:

| Role | RVA / layout |
| --- | --- |
| Visible contexts | count `0x3BBDCF0`, array pointer `0x3BBDD00`, stride `0x3C0` |
| Ordinary segmented vertex count | `0xAF1560`, `4 * cells + 4` |
| Ordinary count call and return | `0xAEC195` / `0xAEC197` |
| Composite vertex count | `0xAF1490`, `8 * cells + 4` |
| Composite count call and return | `0xAEA3C0` / `0xAEA3C2` |
| Ordinary method-pair vtable | `0x2AFBA28` |
| Composite method-pair vtable | `0x2AFBA38` |
| Pool vtable | `0x2B416A8`, map `+0x48`, unmap `+0x50` |
| Pool map / unmap methods | `0xCDD850` / `0xCDD8D0` |

Context offsets `0x190`, `0x1F0`, `0x250`, `0x2B0` are patched only when their
vtable matches the ordinary pair; `0x310` is patched only when it matches the
composite pair. Each clone stores its context offset 16 bytes before the vtable,
so actor tags can be associated with the correct context even for auxiliary bars.

Before recording a range, the callback checks segmentation, finite fraction `[0,1]`,
cell bounds and **both** the original vertex and index capacity conditions. Ordinary
bars permit 1–63 cells; composite bars permit 1–31, keeping diagnostics within the
allocated buffers. Captures are restricted to the two verified count-call return sites.
Other virtual count calls tail-forward their original method.

The count, capacity and pointer arguments at the callback entry are:

| Argument | Ordinary | Composite |
| --- | --- | --- |
| Vertex capacity DWORD | `[rsp+0x158]` | `[rsp+0x168]` |
| Index capacity DWORD | `[rsp+0x160]` | `[rsp+0x170]` |
| Vertex-count pointer | `[rsp+0x168]` | `[rsp+0x178]` |
| Index-count pointer | `[rsp+0x170]` | `[rsp+0x180]` |

RBX supplies the mapped vertex-buffer base. A later recognized bar completes the
pending range; pool unmap completes the last one. A validated range can be followed
by other geometry: only its recorded vertex count is transformed. Decreasing counters,
different buffers/threads and insufficient generated geometry are rejected.

## Installation timing and ABI

The engine reconstructs these objects every frame. A single external vtable write
was consequently overwritten before it could draw. The pool-map callback installs
the clones on the render thread after preparation, before drawing. Three return
addresses restrict that installer to the observed call chain:
`0xCACFB5`, `0xCA43A4`, `0xAF0505`. Up to 4096 contexts may be walked.

The count/unmap callback changes only volatile registers and tail-forwards the original
method. It preserves RCX, the original stack, all nonvolatile GPRs and XMM6–15.
The mapping callback saves/restores RDX/R8/R9 and preserves original stack arguments
and return values. It uses no new thread or DLL loader. Windows x64 native child-process
tests exercise real generated callbacks, not merely byte equality.

The prototype assumes this render path is serialized on its render thread. The pending
range checks its captured thread ID before writing. This is exact-build preview code,
not a general graphics interception framework.

## Allocation/state layout

| Offset | Use |
| --- | --- |
| `0x000` / `0x004` | enabled / pending flags |
| `0x008` | ordinary allowed return address |
| `0x018` | pool object |
| `0x020` / `0x024` | start vertex / completed count |
| `0x028` / `0x030` | buffer / count pointer |
| `0x038` / `0x03C` | inferred center / correction factor |
| `0x040` | captured render thread ID |
| `0x048` / `0x050` | completed groups / skipped ranges |
| `0x080`–`0x0C8` | original methods, return-address gates and context globals |
| `0x0E0` / `0x0E8` | map-frame count / installed pointer count |
| `0x0F0`–`0x0FC` | last tag, fraction and cells |
| `0x100` | completion ring sequence |
| `0x110` / `0x118` / `0x120` | lease deadline / original pool vtable / expiries |
| `0x140`–`0x170` | composite gate/method and additional clone pointers |
| `0x400`–`0x530` | five cloned vtables and per-clone context offsets |
| `0x5F8`–`0x700` | pool vtable copy |
| `0x800` / `0x2800` | before/after diagnostic vertex samples |
| `0x6000` | 32-entry completion sample ring; not a processing limit |
| `0xF000` | RX code page; other allocation pages remain RW |

Normal stop disables the state before restoring current owned pointers and the pool.
Restoration still attempts the pool if context enumeration fails. No unknown pointer
is overwritten. GUI close waits for cleanup and reports an error rather than silently
claiming restoration. Window resizing or pool replacement stops the session.

The GUI worker renews a deadline of current map-frame count + 600 every quarter second.
After worker loss, a later map with an expired lease disables the state and restores
its own pool vtable before forwarding the original map method. This is a render-frame
lease, not an immediate wall-clock shutdown guarantee.

Once pointers have been exposed, the disabled allocation is intentionally retained
until game exit to avoid freeing code still referenced by an in-flight call. Repeated
enable/stop cycles therefore retain another 64 KiB each. There is no startup entry,
service, elevation, anti-cheat bypass, original code-page patch, or game-file change.

## Reproducible checks

```
python -m unittest test_compatibility test_healthbars test_healthbar_native -v
```

These 28 tests do not open the game. The native suite runs generated code only in
isolated Python child processes, including mixed ordinary/composite descriptors,
four ordinary context offsets, capacity guards, original method forwarding, geometry
preservation and expired-lease restoration. It requires Windows x64 and the standard
library, without Capstone or an assembler installation.

Live validation covered the China client at 3440×1440, injury/color changes down to
about 6.7% life, mission transition with replacement contexts, and ordinary/composite
rendering. The user confirmed visual appearance and GUI enable/stop. A bundled EXE
run handled 1408 render frames / 41667 status groups in 15 seconds, with no skipped
ranges, then stopped and restored pointers. These counts describe that bounded run,
not all possible scenes. Actual game-exit cleanup was not forced during user play;
that branch has mocked coverage. Other builds/resolutions/renderers and long-term
stability remain unverified.
