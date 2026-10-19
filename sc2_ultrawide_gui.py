"""Self-contained GUI entry; only an explicit action opens the game process."""
import contextlib
import ctypes
import io
import os
from pathlib import Path
import queue
import sys
import threading
import traceback
import time
import json
from datetime import datetime

import sc2_ultrawide
from ultrawide.ui import APP_DIRECTORY, HELPER_FLAG, helper_log


def health_log(text):
    directory=Path(os.environ.get('SC2_ULTRAWIDE_LOG_DIR',str(Path(APP_DIRECTORY)/'logs')))
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'healthbars.log').open('a',encoding='utf-8') as stream:
        stream.write(f'{datetime.now().astimezone().isoformat()} {text}\n')


def health_test(seconds):
    """Explicit bounded packaged-runtime verification; never runs on GUI launch."""
    from ultrawide.healthbars import HealthBarSession
    result={}
    try:
        with HealthBarSession(log=health_log) as session:
            result.update(pid=session.game.pid,allocation=session.mem)
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                time.sleep(.25)
                stats=session.poll()
                if stats is None:break
                result['statistics']=stats
        result['restored']=True
        health_log('bounded test '+json.dumps(result))
        return 0
    except Exception:
        health_log(traceback.format_exc())
        return 1


def run_command(command):
    output = io.StringIO()
    previous = sys.argv
    code = 0
    try:
        sys.argv = [previous[0], command]
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                sc2_ultrawide.main()
            except SystemExit as error:
                code = error.code if isinstance(error.code, int) else 1
                if error.code and not isinstance(error.code, int):
                    print(error.code)
            except Exception:
                code = 1
                traceback.print_exc()
    finally:
        sys.argv = previous
    text = output.getvalue()
    directory = Path(os.environ.get("SC2_ULTRAWIDE_LOG_DIR", str(Path(APP_DIRECTORY) / "logs")))
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "last-command.txt").write_text(
            f"{datetime.now().astimezone().isoformat()} {command} exit={code}\n{text}", encoding="utf-8")
    except OSError as error:
        code = 1
        text += f"\n无法保存日志：{error}\n"
    return code, text


def main():
    if len(sys.argv)==3 and sys.argv[1]=='--health-test-seconds':
        try:seconds=int(sys.argv[2])
        except ValueError:return 2
        return health_test(seconds) if 1<=seconds<=60 else 2
    if sys.argv[1:] == [HELPER_FLAG]:
        try:
            sc2_ultrawide.main()
            return 0
        except (Exception, SystemExit) as error:
            helper_log(f"helper failed: {type(error).__name__}: {error}")
            return 1
    if sys.argv[1:] == ["--status"]:
        return run_command("status")[0]
    if sys.argv[1:]:
        return 2
    ctypes.windll.user32.SetProcessDPIAware()
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("星际 II 超宽工具 · 血条预览版")
    root.geometry("940x650")
    root.minsize(720, 450)
    panel = ttk.Frame(root, padding=16)
    panel.pack(fill="both", expand=True)
    ttk.Label(panel, text="国服 build 98370 · 单人战役 · 自带 Python 运行时").pack(anchor="w")
    ttk.Label(panel, text="先通过 Battle.net 启动游戏，设为窗口化全屏；进入战役后启用。").pack(anchor="w", pady=(8, 0))
    ttk.Label(panel, text="启用会修改客户端内存，存在账号和崩溃风险。关闭本窗口不停止 HUD 助手；游戏退出后助手结束。").pack(anchor="w", pady=8)
    ttk.Label(panel,text="血条修复为独立实验功能：3440×1440 / build 98370；需保持本窗口打开，关闭时自动停止。").pack(anchor='w')
    bar = ttk.Frame(panel)
    bar.pack(fill="x", pady=8)
    healthbar=ttk.Frame(panel)
    healthbar.pack(fill='x',pady=(0,8))
    health_state=tk.StringVar(value='血条修复：未启用（先进入战役，让单位血条可见）')
    ttk.Label(panel,textvariable=health_state).pack(anchor='w',pady=(0,8))
    state = tk.StringVar(value="等待操作（尚未访问游戏）")
    output = ScrolledText(panel, wrap="word", font=("Consolas", 10))
    output.pack(fill="both", expand=True)
    ttk.Label(panel, textvariable=state).pack(anchor="w", pady=(8, 0))
    results = queue.Queue()
    buttons = []
    busy = False
    health_active=False
    closing=False
    health_stop=threading.Event()
    health_results=queue.Queue()

    def execute(command):
        nonlocal busy
        if busy:
            return
        if health_active and command!='status':
            state.set('请先停止血条修复，再调整超宽窗口。')
            return
        busy = True
        for button in buttons:
            button.configure(state="disabled")
        state.set("正在执行，请稍候……")
        threading.Thread(target=lambda: results.put(run_command(command)), daemon=True).start()

    for label, command in (("只读查看状态", "status"), ("启用超宽 + 自动修复 HUD", "apply"), ("恢复 16:9", "revert")):
        button = ttk.Button(bar, text=label, command=lambda cmd=command: execute(cmd))
        button.pack(side="left", padx=(0, 10))
        buttons.append(button)

    def start_health():
        nonlocal health_active
        if busy or health_active:return
        health_active=True
        health_stop.clear()
        start_button.configure(state='disabled')
        stop_button.configure(state='normal')
        health_state.set('血条修复：正在校验并启用……')
        def worker():
            from ultrawide.healthbars import HealthBarSession
            try:
                with HealthBarSession(log=health_log) as session:
                    health_results.put(('progress','血条修复：已启用；自动跟随换关。保持本窗口打开。'))
                    last=0
                    while not health_stop.wait(.25):
                        stats=session.poll()
                        if stats is None:break
                        if time.monotonic()-last>2:
                            health_results.put(('progress',f'血条修复：已处理 {stats["bars"]} 条状态组 / {stats["frames"]} 帧；跳过 {stats["skipped"]} 次。'))
                            last=time.monotonic()
                health_results.put(('done','血条修复：已停止并恢复；原有超宽和 HUD 功能可继续使用。'))
            except Exception as error:
                try:health_log(traceback.format_exc())
                except OSError:pass
                health_results.put(('error',f'血条修复已停止：{error}'))
        threading.Thread(target=worker,daemon=False).start()

    def stop_health():
        health_stop.set()
        stop_button.configure(state='disabled')
        health_state.set('血条修复：正在停止并恢复……')

    start_button=ttk.Button(healthbar,text='启用血条比例修复（实验）',command=start_health)
    start_button.pack(side='left',padx=(0,10))
    stop_button=ttk.Button(healthbar,text='停止血条修复',command=stop_health,state='disabled')
    stop_button.pack(side='left')

    def poll():
        nonlocal busy,health_active,closing
        try:
            code, text = results.get_nowait()
        except queue.Empty:
            pass
        else:
            busy = False
            output.delete("1.0", "end")
            output.insert("end", text)
            state.set("操作完成" if code == 0 else "操作失败，请查看上方详情")
            for button in buttons:
                button.configure(state="normal")
        while True:
            try:kind,text=health_results.get_nowait()
            except queue.Empty:break
            health_state.set(text)
            if kind in ('done','error'):
                health_active=False
                start_button.configure(state='normal')
                stop_button.configure(state='disabled')
                if closing and kind=='done':
                    root.destroy()
                    return
                closing=False
        root.after(100, poll)

    def close_window():
        nonlocal closing
        if busy:state.set('操作执行中，请等完成后关闭。')
        elif health_active:
            closing=True
            stop_health()
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close_window)
    poll()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
