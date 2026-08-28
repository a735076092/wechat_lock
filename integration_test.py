# -*- coding: utf-8 -*-
"""集成测试：实例化应用，驱动事件循环，验证锁定/解锁完整链路"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wechat_lock as wl
import win32gui
import win32con

app = wl.WeChatLockApp()
print("应用已启动（设置窗口 + 托盘 + 热键 + 监控线程）")

# 驱动事件循环，让 UI 初始化
for _ in range(30):
    app.root.update()
    time.sleep(0.1)

hwnd = wl.find_wechat_window()
print(f"微信窗口: {hwnd}  可见={win32gui.IsWindowVisible(hwnd) if hwnd else 'N/A'}")

# ---- 1. 测试锁定 ----
print("\n=== 测试 do_lock() ===")
app.do_lock()
for _ in range(30):
    app.root.update()
    time.sleep(0.1)
assert app.locked, "locked 标志未设置"
assert app.lock_window is not None, "锁屏窗口未创建"
print(f"locked={app.locked}  锁屏窗={app.lock_window is not None}")
# 微信窗口应被隐藏
if hwnd:
    vis = win32gui.IsWindowVisible(hwnd)
    print(f"微信窗口可见性: {vis}  (应为 False)")
    assert not vis, "微信窗口未被隐藏！"
else:
    print("无微信窗口可隐藏（微信可能驻留托盘）")

# ---- 2. 测试密码解锁 ----
print("\n=== 测试解锁 ===")
# 模拟正确密码
app.lock_window.pwd_entry.insert(0, app.config["password"])
app.lock_window.try_unlock()
for _ in range(30):
    app.root.update()
    time.sleep(0.1)
assert not app.locked, "解锁后 locked 仍为 True"
assert app.lock_window is None, "锁屏窗未关闭"
print(f"locked={app.locked}  锁屏窗已关闭")
if hwnd:
    vis = win32gui.IsWindowVisible(hwnd)
    print(f"微信窗口可见性: {vis}  (应为 True)")
    assert vis, "微信窗口未恢复显示！"

# ---- 3. 测试密码错误 ----
print("\n=== 测试错误密码 ===")
app.do_lock()
for _ in range(30):
    app.root.update()
    time.sleep(0.1)
app.lock_window.pwd_entry.insert(0, "wrongpass")
app.lock_window.try_unlock()
for _ in range(10):
    app.root.update()
    time.sleep(0.1)
assert app.locked, "错误密码不应解锁"
assert app.lock_window is not None
assert app.lock_window.err_label.cget("text") == "密码错误，请重试"
print("错误密码被拒绝，提示正确")

# ---- 4. 监控线程空闲锁定 ----
print("\n=== 测试监控线程自动锁定 ===")
app.config["auto_lock"] = True
app.config["timeout_minutes"] = 1  # 临时改小不影响文件（未保存）
app.last_wechat_active = time.time() - 61  # 模拟已空闲 61 秒
app.check_once()  # 手动触发一次检查
for _ in range(30):
    app.root.update()
    time.sleep(0.1)
assert app.locked, "监控线程未触发自动锁定"
print("监控线程自动锁定触发成功")

# 解锁清理
app.do_unlock()
for _ in range(20):
    app.root.update()
    time.sleep(0.1)

# ---- 5. 微信退出自动解锁 ----
print("\n=== 测试微信退出自动解锁 ===")
app.do_lock()
for _ in range(20):
    app.root.update()
    time.sleep(0.1)
# 模拟微信退出（进程快照返回 False 会走 wechat_gone 分支）
app.wechat_hwnd = None
# 手动模拟：将微信进程结束
import subprocess
subprocess.run(["taskkill", "/IM", "Weixin.exe", "/F"], capture_output=True)
for _ in range(40):
    app.root.update()
    time.sleep(0.2)
assert not app.locked, "微信退出后未自动解锁"
assert app.lock_window is None, "锁屏窗未随微信退出关闭"
print("微信退出后自动解锁，锁屏窗关闭")

app.quit_app()
print("\n=== 集成测试全部通过 ===")
