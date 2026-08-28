# -*- coding: utf-8 -*-
"""
微信空闲锁定器 v1.0
==================================================
功能：
  1. 检测微信空闲（无操作）超过设定时间后自动锁定，默认 5 分钟，可自由设置
  2. 全局快捷键 Ctrl+L 立即锁定微信
  3. 锁定后微信窗口隐藏，弹出密码锁屏窗，输入正确密码后解锁并恢复窗口
  4. 常驻系统托盘，可随时打开设置 / 立即锁定 / 退出

说明：
  - “微信空闲”判定：微信窗口处于前台且有键盘/鼠标输入才算被操作；
    只要超过设定时间没有操作微信（包括微信在后台时），即自动锁定。
  - 兼容微信 3.x (WeChat.exe) 与微信 4.x (Weixin.exe)。
  - 配置文件 wechat_lock_config.json 生成在 exe 同目录。
==================================================
"""

import ctypes
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

import win32api
import win32con
import win32gui
import win32process

APP_NAME = "微信空闲锁定器"
VERSION = "v1.2"
WECHAT_PROCESS_NAMES = ("wechat.exe", "weixin.exe")
HOTKEY_ID = 1
DEFAULT_TIMEOUT_MIN = 5
DEFAULT_PASSWORD = "123456"


# ---------------------------------------------------------------- 基础工具

def base_dir():
    """exe（打包后）或脚本（源码运行时）所在目录"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(base_dir(), "wechat_lock_config.json")


def load_config():
    cfg = {
        "timeout_minutes": DEFAULT_TIMEOUT_MIN,
        "password": DEFAULT_PASSWORD,
        "auto_lock": True,
    }
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                cfg.update(saved)
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 系统 API

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


def system_idle_seconds():
    """系统最后输入距现在的秒数（GetLastInputInfo）"""
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        diff = (win32api.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
        return diff / 1000.0
    return 0.0


def get_process_name(hwnd):
    """返回窗口所属进程的可执行文件名（小写），失败返回空串"""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        hproc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not hproc:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(len(buf))
            ok = kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
            if ok:
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(hproc)
    except Exception:
        pass
    return ""


def find_wechat_window():
    """找到微信主窗口（含最小化到托盘的隐藏窗口）。
    微信 4.0 最小化后主窗口仍存在（隐藏态），取面积最大且非托盘辅助类的窗口。"""
    best_hwnd = None
    best_area = 0

    def _cb(hwnd, _):
        nonlocal best_hwnd, best_area
        if not win32gui.IsWindow(hwnd):
            return True
        if get_process_name(hwnd) not in WECHAT_PROCESS_NAMES:
            return True
        try:
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
        except Exception:
            return True
        # 排除托盘消息窗等辅助窗口
        if "trayicon" in cls.lower() or "trayicon" in title.lower():
            return True
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            area = max(0, r - l) * max(0, b - t)
        except Exception:
            return True
        if area > best_area and area >= 150 * 100:
            best_hwnd, best_area = hwnd, area
        return True

    win32gui.EnumWindows(_cb, None)
    return best_hwnd


def wechat_process_running():
    """通过进程快照检测微信进程是否在运行。
    微信 4.0 关闭到托盘时主窗口会被销毁，必须用进程兜底判断。"""
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if not snap:
        return False
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            return False
        while True:
            if pe.szExeFile.lower() in WECHAT_PROCESS_NAMES:
                return True
            if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return False


# ---------------------------------------------------------------- 全局热键

_hotkey_cb = None


def _hotkey_wndproc(hwnd, msg, wparam, lparam):
    if msg == win32con.WM_HOTKEY and _hotkey_cb is not None:
        try:
            _hotkey_cb()
        except Exception:
            pass
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


class HotkeyThread(threading.Thread):
    """注册全局热键 Ctrl+L 并跑消息循环"""

    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self.ready = threading.Event()
        self.error = None

    def run(self):
        global _hotkey_cb
        _hotkey_cb = self.callback
        try:
            hinst = win32api.GetModuleHandle(None)
            wc = win32gui.WNDCLASS()
            wc.hInstance = hinst
            wc.lpszClassName = "WeChatLockHotkeyWnd"
            wc.lpfnWndProc = _hotkey_wndproc
            atom = win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindowEx(
                win32con.WS_EX_TOOLWINDOW, atom, "WCL", 0,
                0, 0, 0, 0, 0, 0, hinst, None)
            win32gui.RegisterHotKey(hwnd, HOTKEY_ID, win32con.MOD_CONTROL, ord("L"))
            self.ready.set()
        except Exception as e:
            self.error = e
            self.ready.set()
            return
        while True:
            try:
                msg = win32gui.GetMessage(None, 0, 0)
            except Exception:
                break
            if not msg:
                break
            bret, m = msg
            if bret == 0:  # WM_QUIT
                break
            win32gui.TranslateMessage(m)
            win32gui.DispatchMessage(m)


# ---------------------------------------------------------------- 锁屏窗口

class LockWindow:
    W, H = 400, 335  # 含标题栏高度

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("微信已锁定")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#1e2a3a")
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)  # 禁止关闭
        self._build()
        self._center()
        self.win.after(80, lambda: self.pwd_entry.focus_set())

    def _build(self):
        win = self.win
        cv = tk.Canvas(win, width=64, height=66, bg="#1e2a3a", highlightthickness=0)
        cv.pack(pady=(26, 4))
        self._draw_lock(cv)
        tk.Label(win, text="微信已锁定", bg="#1e2a3a", fg="#ffffff",
                 font=("Microsoft YaHei UI", 15, "bold")).pack()
        tk.Label(win, text="微信长时间未操作，已自动锁定，请输入密码解锁",
                 bg="#1e2a3a", fg="#8fa3b8", font=("Microsoft YaHei UI", 9)).pack(pady=(4, 12))
        self.pwd_entry = tk.Entry(win, show="•", font=("Microsoft YaHei UI", 12),
                                  justify="center", width=16, bd=0, relief="flat",
                                  highlightthickness=1, highlightbackground="#3a4a5e",
                                  highlightcolor="#2f6bff", bg="#14202e", fg="#ffffff",
                                  insertbackground="#ffffff")
        self.pwd_entry.pack(ipady=5, padx=40, fill="x", pady=(0, 12))
        self.pwd_entry.bind("<Return>", lambda e: self.try_unlock())
        btn = tk.Button(win, text="解  锁", command=self.try_unlock,
                        bg="#2f6bff", fg="white", activebackground="#1d4fd7",
                        activeforeground="white", relief="flat", cursor="hand2",
                        font=("Microsoft YaHei UI", 10, "bold"), padx=36, pady=4)
        btn.pack()
        self.err_label = tk.Label(win, text="", bg="#1e2a3a", fg="#ff6b6b",
                                  font=("Microsoft YaHei UI", 9))
        self.err_label.pack(pady=(8, 0))

    def _draw_lock(self, cv):
        cv.create_arc(18, 8, 46, 36, start=180, extent=180, style="arc",
                      outline="#ffffff", width=5)
        cv.create_rectangle(12, 30, 52, 62, fill="#2f6bff", outline="#2f6bff")
        cv.create_oval(28, 42, 36, 50, fill="#ffffff", outline="#ffffff")
        cv.create_line(32, 50, 32, 56, fill="#ffffff", width=3)

    def _center(self):
        self.win.update_idletasks()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = (sw - self.W) // 2
        y = (sh - self.H) // 2
        self.win.geometry(f"{self.W}x{self.H}+{x}+{y}")

    def try_unlock(self):
        if self.pwd_entry.get() == self.app.config["password"]:
            self.app.do_unlock()
        else:
            self.err_label.config(text="密码错误，请重试")
            self.pwd_entry.delete(0, "end")
            self.pwd_entry.focus_set()

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass
        self.app.lock_window = None


# ---------------------------------------------------------------- 主应用

class WeChatLockApp:
    def __init__(self):
        self.config = load_config()
        self.locked = False
        self.wechat_running = False
        self.wechat_hwnd = None
        self.last_wechat_active = time.time()
        self.stop_event = threading.Event()
        self.tray = None
        self.lock_window = None
        # 线程安全 UI 事件队列（后台线程禁止直接调用 tkinter）
        self.ui_queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {VERSION}")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_main_close)
        self._build_main_ui()
        self._center_main_window()

        self.hotkey_thread = HotkeyThread(self.do_lock)
        self.hotkey_thread.start()

        threading.Thread(target=self.monitor_loop, daemon=True).start()
        self._start_tray()

        self.root.after(100, self.poll_ui_queue)
        self.root.after(1000, self.update_status)

    # ---------------- 线程安全 UI 调度 ----------------
    def post_ui(self, fn, *args):
        """后台线程调用：把 UI 操作投递到主线程队列"""
        self.ui_queue.put((fn, args))

    def poll_ui_queue(self):
        """主线程轮询，执行后台线程投递的 UI 操作"""
        try:
            while True:
                fn, args = self.ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    pass
        except queue.Empty:
            pass
        if not self.stop_event.is_set():
            self.root.after(100, self.poll_ui_queue)

    # ---------------- 主界面 ----------------
    def _build_main_ui(self):
        root = self.root
        root.configure(bg="#f5f7fa")
        frame = tk.Frame(root, bg="#f5f7fa", padx=26, pady=20)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold"),
                 bg="#f5f7fa", fg="#1f2d3d").pack(anchor="w")
        tk.Label(frame, text="微信空闲超时自动锁定，防止他人查看聊天记录",
                 font=("Microsoft YaHei UI", 9), bg="#f5f7fa", fg="#8492a6").pack(anchor="w", pady=(2, 14))

        self.var_auto = tk.BooleanVar(value=self.config["auto_lock"])
        tk.Checkbutton(frame, text="启用自动锁定", variable=self.var_auto,
                       bg="#f5f7fa", fg="#1f2d3d", activebackground="#f5f7fa",
                       activeforeground="#1f2d3d", font=("Microsoft YaHei UI", 10)).pack(anchor="w")

        row1 = tk.Frame(frame, bg="#f5f7fa")
        row1.pack(anchor="w", pady=(8, 0))
        tk.Label(row1, text="空闲时间：", bg="#f5f7fa", fg="#1f2d3d",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.var_min = tk.IntVar(value=self.config["timeout_minutes"])
        tk.Spinbox(row1, from_=1, to=120, textvariable=self.var_min, width=6,
                   font=("Microsoft YaHei UI", 10), justify="center").pack(side="left", padx=(0, 6))
        tk.Label(row1, text="分钟（1 ~ 120）", bg="#f5f7fa", fg="#8492a6",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")

        row2 = tk.Frame(frame, bg="#f5f7fa")
        row2.pack(anchor="w", pady=(8, 0))
        tk.Label(row2, text="解锁密码：", bg="#f5f7fa", fg="#1f2d3d",
                 font=("Microsoft YaHei UI", 10)).pack(side="left")
        self.var_pwd = tk.StringVar(value=self.config["password"])
        tk.Entry(row2, textvariable=self.var_pwd, show="•", width=18,
                 font=("Microsoft YaHei UI", 10)).pack(side="left")

        tk.Label(frame, text="快捷键：Ctrl + L  立即锁定微信",
                 bg="#f5f7fa", fg="#5b6b7c", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(10, 0))

        tk.Button(frame, text="保存设置", command=self.save_settings,
                  bg="#2f6bff", fg="white", activebackground="#1d4fd7",
                  activeforeground="white", relief="flat", cursor="hand2",
                  padx=28, pady=6, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(16, 0))

        self.status_label = tk.Label(frame, text="状态：检测中…", bg="#f5f7fa", fg="#8492a6",
                                     font=("Microsoft YaHei UI", 9))
        self.status_label.pack(anchor="w", pady=(14, 0))

    def _center_main_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def save_settings(self):
        try:
            minutes = int(self.var_min.get())
            if minutes < 1 or minutes > 120:
                raise ValueError
        except ValueError:
            messagebox.showwarning(APP_NAME, "空闲时间请输入 1 ~ 120 之间的整数分钟")
            return
        pwd = self.var_pwd.get().strip()
        if not pwd:
            messagebox.showwarning(APP_NAME, "解锁密码不能为空")
            return
        self.config["timeout_minutes"] = minutes
        self.config["password"] = pwd
        self.config["auto_lock"] = bool(self.var_auto.get())
        if save_config(self.config):
            self.last_wechat_active = time.time()
            messagebox.showinfo(APP_NAME, "设置已保存")
        else:
            messagebox.showerror(APP_NAME, f"保存失败，请确认程序目录可写：\n{CONFIG_PATH}")

    # ---------------- 状态刷新 ----------------
    def update_status(self):
        if self.stop_event.is_set():
            return
        state = "运行中" if self.wechat_running else "未运行"
        lock = "已锁定" if self.locked else "未锁定"
        extra = ""
        if self.wechat_running and not self.locked and self.config["auto_lock"]:
            remain = self.config["timeout_minutes"] * 60 - (time.time() - self.last_wechat_active)
            if remain > 0:
                extra = f" ｜ {int(remain)} 秒后自动锁定"
            else:
                extra = " ｜ 即将锁定…"
        self.status_label.config(text=f"状态：微信{state} ｜ {lock}{extra}")
        self.root.after(1000, self.update_status)

    # ---------------- 监控逻辑 ----------------
    def monitor_loop(self):
        while not self.stop_event.is_set():
            try:
                self.check_once()
            except Exception:
                pass
            time.sleep(1)

    def check_once(self):
        hwnd = find_wechat_window()
        proc_running = wechat_process_running()
        now = time.time()

        if hwnd:
            # 微信窗口存在（含隐藏态）
            self.wechat_running = True
            self.wechat_hwnd = hwnd
            if not self.locked:
                fg = win32gui.GetForegroundWindow() == hwnd
                idle = system_idle_seconds()
                if fg and idle < 1.5:  # 微信在前台且有输入 → 算操作微信
                    self.last_wechat_active = now
                timeout = self.config["timeout_minutes"] * 60
                if self.config["auto_lock"] and (now - self.last_wechat_active) >= timeout:
                    self.do_lock()
        elif proc_running:
            # 微信进程在运行但主窗口不存在（关闭到托盘/窗口未创建）
            self.wechat_running = True
            self.wechat_hwnd = None
            if not self.locked:
                timeout = self.config["timeout_minutes"] * 60
                if self.config["auto_lock"] and (now - self.last_wechat_active) >= timeout:
                    self.do_lock(no_window=True)
        else:
            # 微信完全退出
            if self.locked:
                self.do_unlock(wechat_gone=True)
            self.wechat_running = False
            self.wechat_hwnd = None
            self.last_wechat_active = now  # 避免微信刚启动就被锁

        if self.locked:
            # 锁定期间持续隐藏微信窗口，防止通过托盘/任务栏绕过
            target = find_wechat_window()
            if target and win32gui.IsWindowVisible(target):
                try:
                    win32gui.ShowWindow(target, win32con.SW_HIDE)
                except Exception:
                    pass
                # 有人试图打开微信（双击托盘图标等）→ 弹出锁屏窗要求输入密码
                self.post_ui(self._raise_lock_window)

    # ---------------- 锁定 / 解锁 ----------------
    def do_lock(self, manual=False, no_window=False):
        if self.locked:
            return
        hwnd = find_wechat_window()
        if not hwnd and not (no_window or wechat_process_running()):
            if manual:
                self.post_ui(lambda: messagebox.showinfo(APP_NAME, "未检测到微信在运行"))
            return
        self.locked = True
        self.wechat_hwnd = hwnd
        if hwnd:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            except Exception:
                pass
        self.post_ui(self._create_lock_window)

    def _create_lock_window(self):
        if self.lock_window and self.lock_window.win.winfo_exists():
            return
        self.lock_window = LockWindow(self)

    def _raise_lock_window(self):
        """把锁屏窗弹到最前面（还原最小化、置顶、聚焦密码框、闪烁提醒）"""
        if not (self.lock_window and self.lock_window.win.winfo_exists()):
            return
        win = self.lock_window.win
        try:
            win.deiconify()          # 还原最小化状态
            win.attributes("-topmost", False)
            win.attributes("-topmost", True)  # 重新置顶
            win.lift()
            win.focus_force()
            self.lock_window.pwd_entry.focus_set()
            # 闪烁任务栏图标提醒
            try:
                user32 = ctypes.windll.user32
                hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
                user32.FlashWindow(hwnd, True)
            except Exception:
                pass
        except Exception:
            pass

    def do_unlock(self, wechat_gone=False):
        if not self.locked:
            return
        self.locked = False
        self.wechat_hwnd = None
        self.last_wechat_active = time.time()
        self.post_ui(self._finish_unlock, wechat_gone)

    def _finish_unlock(self, wechat_gone):
        if self.lock_window:
            self.lock_window.close()
            self.lock_window = None
        if not wechat_gone:
            hwnd = find_wechat_window()
            if hwnd:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    if not win32gui.IsWindowVisible(hwnd):
                        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    pass

    # ---------------- 托盘 ----------------
    def _start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            self.tray = None
            return

        def make_image():
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle([8, 8, 56, 56], radius=14, fill=(47, 107, 255, 255))
            d.arc([22, 12, 42, 32], 180, 360, fill=(255, 255, 255, 255), width=5)
            d.rounded_rectangle([18, 28, 46, 52], radius=5, fill=(255, 255, 255, 255))
            d.ellipse([28, 36, 36, 44], fill=(47, 107, 255, 255))
            return img

        menu = pystray.Menu(
            pystray.MenuItem("打开设置", lambda: self.post_ui(self.show_main_window)),
            pystray.MenuItem("立即锁定微信", lambda: self.do_lock(manual=True)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit_app),
        )
        try:
            self.tray = pystray.Icon("wechat_idle_lock", make_image(), APP_NAME, menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception:
            self.tray = None

    def show_main_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_main_close(self):
        self.root.withdraw()
        if self.tray is not None:
            try:
                self.tray.notify("程序仍在后台运行，可点击托盘图标打开设置", APP_NAME)
            except Exception:
                pass

    def quit_app(self, icon=None, item=None):
        self.stop_event.set()
        try:
            if self.tray is not None:
                self.tray.stop()
        except Exception:
            pass
        self.post_ui(self.root.destroy)
        time.sleep(0.3)
        os._exit(0)


# ---------------------------------------------------------------- 入口

def main():
    # 单实例互斥，防止重复启动
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "WeChatIdleLockMutex")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            messagebox.showwarning(APP_NAME, "程序已在运行，请在系统托盘查看")
            return
    except Exception:
        pass

    app = WeChatLockApp()

    if app.hotkey_thread.ready.wait(timeout=3) and app.hotkey_thread.error:
        try:
            app.tray.notify("提示：Ctrl+L 快捷键注册失败（可能被其他程序占用）", APP_NAME)
        except Exception:
            pass

    app.root.mainloop()


if __name__ == "__main__":
    main()
