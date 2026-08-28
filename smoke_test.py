# -*- coding: utf-8 -*-
"""冒烟测试：验证核心函数，不启动 GUI"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_lock as wl

print("=== 1. 系统空闲检测 ===")
print(f"system_idle_seconds() = {wl.system_idle_seconds():.2f} 秒")
assert wl.system_idle_seconds() >= 0

print("\n=== 2. 查找微信主窗口（当前微信 4.0 正在运行） ===")
hwnd = wl.find_wechat_window()
print(f"find_wechat_window() -> {hwnd}")
proc = wl.wechat_process_running()
print(f"wechat_process_running() -> {proc}")
assert proc, "微信进程在运行但检测失败！"
if hwnd:
    import win32gui
    print(f"  窗口标题: {win32gui.GetWindowText(hwnd)!r}")
    print(f"  窗口类名: {win32gui.GetClassName(hwnd)!r}")
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    print(f"  窗口尺寸: {r-l} x {b-t}")
    print(f"  可见: {win32gui.IsWindowVisible(hwnd)}")
    pid_name = wl.get_process_name(hwnd)
    print(f"  所属进程: {pid_name}")
    assert pid_name in wl.WECHAT_PROCESS_NAMES
else:
    print("  当前无微信主窗口（可能驻留托盘），但进程检测已兜底")

print("\n=== 3. 热键注册测试（Ctrl+L） ===")
result = {}
def on_hotkey():
    result["fired"] = True
ht = wl.HotkeyThread(on_hotkey)
ht.start()
assert ht.ready.wait(timeout=3), "热键线程未就绪"
if ht.error:
    print(f"  热键注册失败: {ht.error}")
else:
    print("  热键注册成功，模拟按下 Ctrl+L ...")
    import ctypes
    # 模拟按下 Ctrl+L
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)   # Ctrl down
    ctypes.windll.user32.keybd_event(0x4C, 0, 0, 0)   # L down
    ctypes.windll.user32.keybd_event(0x4C, 0, 2, 0)   # L up
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)   # Ctrl up
    time.sleep(1.5)
    print(f"  热键回调触发: {result.get('fired', False)}")
    assert result.get("fired"), "热键回调未触发"

print("\n=== 4. 配置读写 ===")
cfg = wl.load_config()
print(f"  默认配置: {cfg}")
cfg["timeout_minutes"] = 7
assert wl.save_config(cfg)
cfg2 = wl.load_config()
assert cfg2["timeout_minutes"] == 7
print("  配置读写 OK")
# 恢复
cfg["timeout_minutes"] = 5
wl.save_config(cfg)

print("\n=== 全部冒烟测试通过 ===")
