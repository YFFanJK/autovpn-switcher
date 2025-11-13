# -*- coding: utf-8 -*-
# AutoVPN Switcher - 专业托盘版
# 依赖: pip install pystray pillow

import tkinter as tk
from tkinter import messagebox, scrolledtext
import subprocess
import time
import threading
import urllib.request
import json
import os
import sys
from datetime import datetime
import pystray
from PIL import Image
import ctypes
import winreg

# 全局互斥句柄（仅 Windows 有效）
_mutex_handle = None

def acquire_mutex(name="AutoVPN_Mutex_01"):
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        # CreateMutexW 返回句柄，若已存在，可通过 GetLastError 判断
        handle = kernel32.CreateMutexW(None, False, ctypes.c_wchar_p(name))
        if not handle:
            return True
        last = kernel32.GetLastError()
        ERROR_ALREADY_EXISTS = 183
        if last == ERROR_ALREADY_EXISTS:
            try:
                kernel32.CloseHandle(handle)
            except:
                pass
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True


def release_mutex():
    global _mutex_handle
    if sys.platform != "win32":
        return
    try:
        if _mutex_handle:
            ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
    except:
        pass

# ==================== 配置区 ====================
def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(__file__)

BASE_PATH = get_base_path()
CONFIG_FILE = os.path.join(BASE_PATH, "autovpn_config.json")
LOG_FILE = os.path.join(BASE_PATH, "autovpn.log")
ICON_PNG = os.path.join(BASE_PATH, "icon.png")  # 可选：放一个 64x64 PNG 图标
ICON_ICO = os.path.join(BASE_PATH, "icon.ico")  # 优先使用 ico（用于窗口与托盘）

DEFAULT_CONFIG = {
    "rules": [
        {"ssids": "公司WiFi,Office-5G", "mode": "Direct"},
        {"ssids": "*", "mode": "Rule"}
    ],
    "api_url": "http://127.0.0.1:9090/configs",
    "interval": 15,
    "autostart": False
}

# 隐藏控制台窗口（打包后）
if getattr(sys, 'frozen', False) and sys.platform == "win32":
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

# =======================================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 合并默认值
                config = DEFAULT_CONFIG.copy()
                config.update(data)
                return config
        except Exception as e:
            print(f"配置文件加载失败: {e}")
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except:
        return False

def log(msg, widget=None):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    if widget and widget.winfo_exists():
        widget.insert(tk.END, line)
        widget.see(tk.END)
    print(line.strip())
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
        trim_log_file()
    except:
        pass

def trim_log_file(max_lines=1000):
    """保留最近 1000 行日志"""
    try:
        if not os.path.exists(LOG_FILE):
            return
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-max_lines:])
    except:
        pass

def get_ssid():
    try:
        creation_flags = 0
        if sys.platform == 'win32':
            creation_flags = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=creation_flags
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("SSID") and ":" in line:
                ssid = line.split(":", 1)[1].strip()
                ssid = ''.join(c for c in ssid if ord(c) < 0x10000)
                return ssid if ssid and not ssid.startswith("BSSID") else None
        return None
    except:
        return None

def set_clash_mode(mode, api_url):
    data = json.dumps({"mode": mode}).encode("utf-8")
    req = urllib.request.Request(api_url, data=data, method="PATCH")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 204, f"模式切换: {mode}"
    except Exception as e:
        return False, f"连接失败: {e}"

def match_rule(ssid, rules):
    """返回匹配的 mode，或 None"""
    if not ssid:
        ssid = ""
    ssid_lower = ssid.lower()
    for rule in rules:
        ssids = [s.strip().lower() for s in rule["ssids"].split(",")]
        if "*" in ssids or any(s in ssid_lower for s in ssids if s):
            return rule["mode"]
    return None

def monitor_loop(rules, api, interval, log_widget, stop_event, status_callback, mode_callback):
    last_ssid = None
    current_mode = None
    log("监控已启动", log_widget)
    status_callback("运行中", "#27ae60")

    while not stop_event.is_set():
        try:
            ssid = get_ssid()

            if ssid != last_ssid:
                log(f"WiFi: {ssid or '未连接'}", log_widget)
                last_ssid = ssid

            target = match_rule(ssid, rules) or "Rule"

            if target != current_mode:
                ok, msg = set_clash_mode(target, api)
                log(f"{'成功' if ok else '失败'} {msg}", log_widget)
                if ok:
                    current_mode = target
                    mode_callback(current_mode, False)

            stop_event.wait(interval)
        except Exception as e:
            log(f"E 监控错误: {e}", log_widget)
            stop_event.wait(interval)

    log("监控已停止", log_widget)
    status_callback("已停止", "#95a5a6")

# ==================== 托盘 & 热键 ====================
def set_autostart(enable):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enable:
            winreg.SetValueEx(key, "AutoVPN", 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, "AutoVPN")
            except:
                pass
        winreg.CloseKey(key)
    except:
        pass

class App:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.thread = None
        self.stop_event = threading.Event()
        self.log_window = None

        root.title("AutoVPN 切换器")
        root.geometry("400x500")
        root.minsize(380, 240)
        root.resizable(True, True)
        root.configure(bg="#f5f7fa")

        # 尝试加载目录下的 ico 优先作为窗口图标，若没有则尝试 png
        try:
            if os.path.exists(ICON_ICO):
                try:
                    root.iconbitmap(ICON_ICO)
                except:
                    pass
            elif os.path.exists(ICON_PNG):
                try:
                    self._icon_photo = tk.PhotoImage(file=ICON_PNG)
                    root.iconphoto(False, self._icon_photo)
                except:
                    pass
        except:
            pass

        # 设置窗口权重，使内容自适应
        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)

        self._create_ui()
        self.setup_tray()
        self.load_autostart()

    def _create_ui(self):
        # 主容器使用网格布局
        main_container = tk.Frame(self.root, bg="#f5f7fa")
        main_container.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        main_container.grid_rowconfigure(0, weight=0)  # 头部
        main_container.grid_rowconfigure(1, weight=0)  # 状态
        main_container.grid_rowconfigure(2, weight=0)  # 按钮
        main_container.grid_rowconfigure(3, weight=1)  # 空白
        main_container.grid_columnconfigure(0, weight=1)

        self._create_header(main_container)
        self._create_status_section(main_container)
        self._create_buttons(main_container)

    def _create_header(self, parent):
        header = tk.Frame(parent, bg="#2c3e50", height=110)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(0, weight=1)
        header.pack_propagate(False)
        
        inner = tk.Frame(header, bg="#2c3e50")
        inner.pack(expand=True, fill="both")
        
        tk.Label(inner, text="⚔️", font=("Segoe UI Emoji", 36), bg="#2c3e50", fg="white").pack(pady=(8, 0))
        tk.Label(inner, text="VPN 切换器", font=("Microsoft YaHei UI", 18, "bold"), bg="#2c3e50", fg="white").pack(pady=(2, 0))
        tk.Label(inner, text="智能多策略 VPN 切换系统", font=("Microsoft YaHei UI", 9), bg="#2c3e50", fg="#bdc3c7").pack(pady=(0, 8))

    def _create_status_section(self, parent):
        frame = tk.Frame(parent, bg="#ffffff", highlightbackground="#dfe4ea", highlightthickness=1)
        frame.grid(row=1, column=0, sticky="ew", padx=15, pady=(15, 15))
        frame.grid_columnconfigure(0, weight=1)
        
        inner = tk.Frame(frame, bg="#ffffff")
        inner.pack(fill="both", expand=False, padx=15, pady=12)
        
        # 状态行
        status_row = tk.Frame(inner, bg="#ffffff")
        status_row.pack(fill="x", padx=0, pady=(0, 8))
        tk.Label(status_row, text="运行状态:", font=("Microsoft YaHei UI", 11), bg="#ffffff", fg="#34495e").pack(side="left", padx=(0, 10))
        self.status_label = tk.Label(status_row, text="未启动", font=("Microsoft YaHei UI", 11, "bold"), bg="#ffffff", fg="#95a5a6")
        self.status_label.pack(side="left", fill="x", expand=True)
        
        # 模式行
        mode_row = tk.Frame(inner, bg="#ffffff")
        mode_row.pack(fill="x", padx=0, pady=(0, 0))
        tk.Label(mode_row, text="当前模式:", font=("Microsoft YaHei UI", 11), bg="#ffffff", fg="#34495e").pack(side="left", padx=(0, 10))
        self.mode_label = tk.Label(mode_row, text="--", font=("Microsoft YaHei UI", 11, "bold"), bg="#ffffff", fg="#2c3e50")
        self.mode_label.pack(side="left", fill="x", expand=True)

    def _create_buttons(self, parent):
        frame = tk.Frame(parent, bg="#f5f7fa")
        frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(0, 15))
        frame.grid_columnconfigure(0, weight=1)
        
        btn_inner = tk.Frame(frame, bg="#f5f7fa")
        btn_inner.pack(expand=False, pady=10, fill="x")
        
        # 使用 grid 布局使按钮能自适应换行
        btn_frame = tk.Frame(btn_inner, bg="#f5f7fa")
        btn_frame.pack(fill="x")
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_columnconfigure(2, weight=1)
        btn_frame.grid_columnconfigure(3, weight=1)
        
        tk.Button(btn_frame, text="▶ 启动", command=self.start, bg="#27ae60", fg="white", relief="flat", padx=10, pady=10, font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=0, padx=4, pady=2, sticky="ew")
        tk.Button(btn_frame, text="⏹ 停止", command=self.stop, bg="#e74c3c", fg="white", relief="flat", padx=10, pady=10, font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=1, padx=4, pady=2, sticky="ew")
        tk.Button(btn_frame, text="⚙ 设置", command=self.open_settings, bg="#3498db", fg="white", relief="flat", padx=10, pady=10, font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=2, padx=4, pady=2, sticky="ew")
        tk.Button(btn_frame, text="📋 日志", command=self.open_log_window, bg="#9b59b6", fg="white", relief="flat", padx=10, pady=10, font=("Microsoft YaHei UI", 10, "bold")).grid(row=0, column=3, padx=4, pady=2, sticky="ew")

    def update_status(self, text, color):
        if self.status_label.winfo_exists():
            self.root.after(0, lambda: self.status_label.config(text=f"{text}", fg=color))

    def update_mode(self, mode, is_silent=False):
        if self.mode_label.winfo_exists():
            self.root.after(0, lambda: self.mode_label.config(text=f"模式: {mode}"))

    def start(self):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("提示", "监控已在运行")
            return

        api = self.config.get("api_url", "").strip()
        interval = self.config.get("interval", 15)

        if not api:
            messagebox.showerror("错误", "请先在设置中填写 API 地址")
            return

        self.stop_event.clear()
        self.thread = threading.Thread(
            target=monitor_loop,
            args=(self.config["rules"], api, interval, self.log_box if (self.log_window and self.log_window.winfo_exists()) else None, self.stop_event, self.update_status, self.update_mode),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def open_settings(self):
        """打开设置窗口"""
        settings_win = tk.Toplevel(self.root)
        settings_win.title("设置")
        settings_win.geometry("550x500")
        settings_win.minsize(450, 400)
        settings_win.transient(self.root)
        settings_win.grab_set()

        # 主容器
        main = tk.Frame(settings_win, bg="#f5f7fa")
        main.pack(fill="both", expand=True, padx=0, pady=0)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # 头部
        header = tk.Frame(main, bg="#2c3e50", height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="⚙ 设置", font=("Microsoft YaHei UI", 16, "bold"), bg="#2c3e50", fg="white").pack(pady=15)

        # 内容区 - 使用滚动框架
        scroll_frame = ScrollableFrame(main, bg="#f5f7fa")
        scroll_frame.pack(fill="both", expand=True, pady=20, padx=20)
        content = scroll_frame.get_frame()
        content.configure(bg="#f5f7fa")

        # 规则部分
        rules_frame = tk.LabelFrame(content, text="多策略规则", font=("Microsoft YaHei UI", 12, "bold"), bg="#ffffff", fg="#2c3e50", padx=15, pady=15)
        rules_frame.pack(fill="x", pady=(0, 15), padx=0)

        self.rules_text_settings = tk.Text(rules_frame, height=4, font=("Consolas", 9), relief="flat", bd=1, bg="#f8f9fa", padx=10, pady=8)
        self.rules_text_settings.pack(fill="both", expand=True, pady=(0, 10))
        self._update_rules_text_settings()

        rules_btn = tk.Frame(rules_frame, bg="#ffffff")
        rules_btn.pack(fill="x")
        tk.Button(rules_btn, text="添加规则", command=self.add_rule_settings, bg="#3498db", fg="white", relief="flat", padx=12, pady=6, font=("Microsoft YaHei UI", 10)).pack(side="right", padx=(5, 0))
        tk.Button(rules_btn, text="保存规则", command=lambda: self.save_rules_settings(settings_win), bg="#27ae60", fg="white", relief="flat", padx=12, pady=6, font=("Microsoft YaHei UI", 10)).pack(side="right", padx=5)

        # API 部分
        api_frame = tk.LabelFrame(content, text="Clash API 配置", font=("Microsoft YaHei UI", 12, "bold"), bg="#ffffff", fg="#2c3e50", padx=15, pady=15)
        api_frame.pack(fill="x", padx=0, pady=(0, 15))
        
        tk.Label(api_frame, text="API 地址:", font=("Microsoft YaHei UI", 11), bg="#ffffff", fg="#34495e").pack(anchor="w", pady=(0, 5))
        self.e_api_settings = tk.Entry(api_frame, font=("Microsoft YaHei UI", 10), relief="flat", bd=1, bg="#f8f9fa", highlightthickness=0)
        self.e_api_settings.pack(fill="x", padx=10, pady=8)
        self.e_api_settings.insert(0, self.config["api_url"])

        # 间隔部分
        interval_frame = tk.Frame(content, bg="#ffffff")
        interval_frame.pack(fill="x", padx=15, pady=15)
        tk.Label(interval_frame, text="检查间隔 (秒):", font=("Microsoft YaHei UI", 11), bg="#ffffff", fg="#34495e").pack(anchor="w", pady=(0, 5))
        self.e_int_settings = tk.Entry(interval_frame, font=("Microsoft YaHei UI", 10), relief="flat", bd=1, bg="#f8f9fa", highlightthickness=0, width=10)
        self.e_int_settings.pack(anchor="w", padx=10, pady=8)
        self.e_int_settings.insert(0, str(self.config["interval"]))

        # 开机启动
        v = tk.BooleanVar(value=self.config.get("autostart", False))
        chk = tk.Checkbutton(content, text="开机启动", variable=v,
                             command=lambda: [self.config.update(autostart=v.get()), set_autostart(v.get()), save_config(self.config)],
                             bg="#f5f7fa", fg="#2c3e50", activebackground="#f5f7fa", selectcolor="#ffffff", font=("Microsoft YaHei UI", 11))
        chk.pack(anchor="w", padx=15, pady=15)

        # 底部保存按钮 - 固定在底部
        btn_frame = tk.Frame(main, bg="#f5f7fa")
        btn_frame.pack(fill="x", padx=20, pady=(0, 20))
        tk.Button(btn_frame, text="保存所有设置", command=lambda: self.save_all_settings_settings(settings_win), bg="#27ae60", fg="white", relief="flat", padx=20, pady=10, font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x")

    def _update_rules_text_settings(self):
        self.rules_text_settings.delete("1.0", tk.END)
        for r in self.config["rules"]:
            self.rules_text_settings.insert(tk.END, f"{r['ssids']} → {r['mode']}\n")

    def add_rule_settings(self):
        """添加规则（设置窗口版本）"""
        win = tk.Toplevel(self.root)
        win.title("添加规则")
        win.geometry("400x280")
        win.minsize(350, 250)
        win.transient(self.root)
        win.grab_set()

        main = tk.Frame(win, bg="#f5f7fa")
        main.pack(fill="both", expand=True)

        # 头部
        header = tk.Frame(main, bg="#2c3e50")
        header.pack(fill="x", padx=0, pady=0)
        tk.Label(header, text="➕ 添加规则", font=("Microsoft YaHei UI", 12, "bold"), bg="#2c3e50", fg="white").pack(pady=10)

        # 内容
        content = tk.Frame(main, bg="#f5f7fa")
        content.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(content, text="WiFi名称 (逗号分隔):", font=("Microsoft YaHei UI", 11), bg="#f5f7fa", fg="#34495e").pack(anchor="w", pady=(0, 5))
        e1 = tk.Entry(content, width=40, font=("Microsoft YaHei UI", 10), relief="flat", bd=1, bg="#ffffff")
        e1.pack(fill="x", pady=(0, 15))
        
        tk.Label(content, text="代理模式:", font=("Microsoft YaHei UI", 11), bg="#f5f7fa", fg="#34495e").pack(anchor="w", pady=(0, 8))
        mode = tk.StringVar(value="Rule")
        frame_mode = tk.Frame(content, bg="#f5f7fa")
        frame_mode.pack(fill="x", pady=(0, 15))
        tk.Radiobutton(frame_mode, text="直连 (Direct)", variable=mode, value="Direct", font=("Microsoft YaHei UI", 10), bg="#f5f7fa").pack(anchor="w")
        tk.Radiobutton(frame_mode, text="规则 (Rule)", variable=mode, value="Rule", font=("Microsoft YaHei UI", 10), bg="#f5f7fa").pack(anchor="w")
        tk.Radiobutton(frame_mode, text="全局 (Global)", variable=mode, value="Global", font=("Microsoft YaHei UI", 10), bg="#f5f7fa").pack(anchor="w")

        # 按钮
        btn_frame = tk.Frame(main, bg="#f5f7fa")
        btn_frame.pack(fill="x", padx=20, pady=20)

        def ok():
            ssids = e1.get().strip()
            if ssids:
                self.config["rules"].insert(-1, {"ssids": ssids, "mode": mode.get()})
                self._update_rules_text_settings()
                win.destroy()
            else:
                messagebox.showwarning("提示", "请输入WiFi名称")
        
        tk.Button(btn_frame, text="确定", command=ok, bg="#27ae60", fg="white", relief="flat", padx=20, pady=10, font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", side="left", padx=(0, 5))
        tk.Button(btn_frame, text="取消", command=win.destroy, bg="#95a5a6", fg="white", relief="flat", padx=20, pady=10, font=("Microsoft YaHei UI", 11, "bold")).pack(fill="x", side="left", padx=5)

    def save_rules_settings(self, parent):
        """保存规则（设置窗口版本）"""
        try:
            text = self.rules_text_settings.get("1.0", tk.END).strip()
            rules = []
            for line in text.splitlines():
                if "→" in line:
                    ssids, mode = line.split("→", 1)
                    rules.append({"ssids": ssids.strip(), "mode": mode.strip()})
            if not any("*" in r["ssids"] for r in rules):
                rules.append({"ssids": "*", "mode": "Rule"})
            self.config["rules"] = rules
            messagebox.showinfo("成功", "规则已保存")
        except:
            messagebox.showerror("错误", "规则格式错误")

    def save_all_settings_settings(self, parent):
        """保存所有设置"""
        try:
            api = self.e_api_settings.get().strip()
            try:
                interval = max(3, int(self.e_int_settings.get()))
            except:
                interval = 15

            if not api:
                messagebox.showerror("错误", "请填写 API 地址")
                return

            self.config.update({"api_url": api, "interval": interval})
            self.save_rules_settings(parent)
            save_config(self.config)
            messagebox.showinfo("成功", "所有设置已保存")
            parent.destroy()
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def open_log_window(self):
        """打开日志窗口"""
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("日志")
        self.log_window.geometry("750x600")
        self.log_window.minsize(500, 300)
        self.log_window.protocol("WM_DELETE_WINDOW", self.close_log_window)

        # 主容器
        main = tk.Frame(self.log_window, bg="#f5f7fa")
        main.pack(fill="both", expand=True, padx=0, pady=0)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # 头部
        header = tk.Frame(main, bg="#2c3e50", height=50)
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.pack_propagate(False)
        tk.Label(header, text="📋 日志", font=("Microsoft YaHei UI", 14, "bold"), bg="#2c3e50", fg="white").pack(pady=12)

        # 日志框 - 自适应填充可用空间
        log_frame = tk.Frame(main, bg="#ffffff")
        log_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        self.log_box = scrolledtext.ScrolledText(log_frame, font=("Consolas", 10), relief="flat", bd=0, bg="#2c3e50", fg="#ecf0f1", insertbackground="#ecf0f1", padx=10, pady=8)
        self.log_box.grid(row=0, column=0, sticky="nsew")

        # 底部按钮
        btn_frame = tk.Frame(main, bg="#f5f7fa")
        btn_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        btn_frame.grid_columnconfigure(0, weight=1)
        
        btn_inner = tk.Frame(btn_frame, bg="#f5f7fa")
        btn_inner.pack()
        
        tk.Button(btn_inner, text="清空日志", command=self.clear_log, bg="#e74c3c", fg="white", relief="flat", padx=15, pady=8, font=("Microsoft YaHei UI", 10)).pack(side="left", padx=5)
        tk.Button(btn_inner, text="关闭", command=self.close_log_window, bg="#95a5a6", fg="white", relief="flat", padx=15, pady=8, font=("Microsoft YaHei UI", 10)).pack(side="left", padx=5)

        # 输出初始信息
        log(f"配置加载: {CONFIG_FILE}", self.log_box)

    def close_log_window(self):
        """关闭日志窗口"""
        if self.log_window:
            self.log_window.destroy()
            self.log_window = None

    def clear_log(self):
        """清空日志"""
        if self.log_box and self.log_box.winfo_exists():
            self.log_box.delete("1.0", tk.END)

    def setup_tray(self):
        # 托盘图标：优先使用 ico，然后 png，最后回退为简易生成图
        if os.path.exists(ICON_ICO):
            try:
                image = Image.open(ICON_ICO)
            except:
                image = None
        elif os.path.exists(ICON_PNG):
            try:
                image = Image.open(ICON_PNG)
            except:
                image = None
        else:
            image = None

        if image is None:
            image = Image.new("RGB", (64, 64), "#34495e")
            from PIL import ImageDraw, ImageFont
            d = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("seguiemj.ttf", 40)
            except:
                font = ImageFont.load_default()
            d.text((10, 10), "Shield", fill="white", font=font)

        menu = pystray.Menu(
            pystray.MenuItem("显示", self.show_window),
            pystray.MenuItem("退出", self.quit_app)
        )
        self.icon = pystray.Icon("AutoVPN", image, "VPN 切换器", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon=None, item=None):
        self.root.after(0, lambda: [self.root.deiconify(), self.root.lift()])

    def quit_app(self, icon=None, item=None):
        # 触发停止，停止托盘并释放互斥，最后退出主循环
        try:
            self.stop_event.set()
            # 等待后台线程短暂结束
            try:
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=2)
            except:
                pass
            if getattr(self, 'icon', None):
                try:
                    self.icon.stop()
                except:
                    pass
            try:
                release_mutex()
            except:
                pass
            try:
                self.root.quit()
                self.root.destroy()
            except:
                pass
            try:
                sys.exit(0)
            except:
                pass
        except Exception:
            pass

    def on_close(self):
        # 点击窗口 X 时直接退出程序（关闭后台、托盘、释放锁）
        self.quit_app()

    def load_autostart(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
            winreg.QueryValueEx(key, "AutoVPN")
            self.config["autostart"] = True
        except:
            self.config["autostart"] = False

# ==================== UI 组件 ====================
class ModernEntry(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#f5f7fa", **kwargs)
        self.entry = tk.Entry(self, font=("Microsoft YaHei UI", 11), relief="flat", bd=0, bg="#ffffff", highlightthickness=1, highlightbackground="#dfe4ea", insertbackground="#2c3e50")
        self.entry.pack(fill="both", expand=True, padx=10, pady=8)

    def get(self):
        return self.entry.get()

    def insert(self, index, string):
        self.entry.insert(index, string)
    
    def config(self, **kwargs):
        self.entry.config(**kwargs)


class ScrollableFrame(tk.Frame):
    """可滚动的框架，用于处理内容溢出"""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        
        # 创建画布和滚动条
        self.canvas = tk.Canvas(self, bg=parent.cget("bg"), highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=self.canvas.cget("bg"))
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # 支持鼠标滚轮
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def get_frame(self):
        return self.scrollable_frame

# ==================== 主程序 ====================
if __name__ == "__main__":
    # 先获取单实例锁，防止重复打开
    if not acquire_mutex("AutoVPN_SingleInstance_Mutex"):
        try:
            # Windows 原生消息框（避免在无 GUI 上出现问题）
            ctypes.windll.user32.MessageBoxW(None, "程序已在运行中", "提示", 0)
        except:
            print("程序已在运行中")
        sys.exit(0)

    root = tk.Tk()
    app = App(root)
    # 点击 X 时退出（而不是隐藏）以确保后台线程与托盘被清理
    root.protocol("WM_DELETE_WINDOW", app.quit_app)
    try:
        root.mainloop()
    finally:
        # 确保退出时释放互斥（保险起见）
        try:
            release_mutex()
        except:
            pass
