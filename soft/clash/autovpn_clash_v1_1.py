# -*- coding: utf-8 -*-
# AutoVPN Switcher - 专业托盘版 (UI 优化版 - 修复 ScrollableFrame Bug & 增加 API 测试)
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
from PIL import Image, ImageDraw, ImageFont 
import ctypes
import winreg

# ==================== 样式常量 ====================
class Style:
    # 颜色
    BG_LIGHT = "#f5f7fa"       # 应用程序背景
    BG_WHITE = "#ffffff"       # 卡片背景
    PRIMARY = "#007bff"        # 主色调 (蓝色)
    PRIMARY_HOVER = "#0056b3"  # 主色调深色 (用于按钮点击/悬停)
    ACCENT_DARK = "#34495e"    # 深色文本/控件
    ACCENT_LIGHT = "#95a5a6"   # 次要文本
    SUCCESS = "#28a745"        # 运行中/成功
    ERROR = "#dc3545"          # 错误/停止
    WARNING = "#ffc107"        # 警告/提示
    
    # 字体
    FONT_TITLE = ("Microsoft YaHei UI", 18, "bold")
    FONT_SUBTITLE = ("Microsoft YaHei UI", 10)
    FONT_BODY = ("Microsoft YaHei UI", 11)
    FONT_BUTTON = ("Microsoft YaHei UI", 10, "bold")
    FONT_MONO = ("Consolas", 10)
    
# ==================== 互斥锁（单实例） ====================

# 全局互斥句柄（仅 Windows 有效）
_mutex_handle = None

def acquire_mutex(name="AutoVPN_Mutex_01"):
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
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

# ==================== 配置和日志 ====================

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(__file__)

BASE_PATH = get_base_path()
CONFIG_FILE = os.path.join(BASE_PATH, "autovpn_config.json")
LOG_FILE = os.path.join(BASE_PATH, "autovpn.log")
ICON_PNG = os.path.join(BASE_PATH, "icon.png")  
ICON_ICO = os.path.join(BASE_PATH, "icon.ico")  

DEFAULT_CONFIG = {
    "rules": [
        {"ssids": "公司WiFi,Office-5G", "mode": "Direct"},
        {"ssids": "*", "mode": "Rule"}
    ],
    "api_url": "http://127.0.0.1:9090/configs",
    "interval": 15,
    "autostart": False
}

if getattr(sys, 'frozen', False) and sys.platform == "win32":
    ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
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

# ==================== 核心逻辑 ====================

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
                # 过滤掉无法编码的字符
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
        # 优化匹配逻辑：使用完整的 WiFi 名称匹配或通配符
        if "*" in ssids or any(ssid_lower == s for s in ssids if s and s != '*'):
            return rule["mode"]
    return None

def monitor_loop(rules, api, interval, log_widget, stop_event, status_callback, mode_callback):
    last_ssid = None
    current_mode = None
    log("监控已启动", log_widget)
    status_callback("运行中", Style.SUCCESS)

    while not stop_event.is_set():
        try:
            ssid = get_ssid()

            if ssid != last_ssid:
                log(f"WiFi: {ssid or '未连接'}", log_widget)
                last_ssid = ssid

            # 默认规则
            target = match_rule(ssid, rules)
            if target is None:
                # 如果没有匹配到任何规则，包括通配符，则使用默认的 'Rule'
                target = 'Rule'

            if target != current_mode:
                ok, msg = set_clash_mode(target, api)
                log(f"{'成功' if ok else '失败'} {msg}", log_widget)
                if ok:
                    current_mode = target
                    mode_callback(current_mode) 

            stop_event.wait(interval)
        except Exception as e:
            log(f"E 监控错误: {e}", log_widget)
            stop_event.wait(interval)

    log("监控已停止", log_widget)
    status_callback("已停止", Style.ACCENT_LIGHT)

# ==================== UI 组件 ====================

class ScrollableFrame(tk.Frame):
    """可滚动的框架，用于处理内容溢出 (修复了 TclError)"""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.canvas = tk.Canvas(self, bg=parent.cget("bg"), highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=self.canvas.cget("bg"))
        
        # 绑定 inner frame 的大小变化来更新 scrollregion
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        # 存储 create_window 返回的 item ID，这是修复 TclError 的关键
        self.window_item_id = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw") 
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # 绑定 Canvas 的大小变化来更新 window item 的宽度
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # 支持鼠标滚轮
        if sys.platform.startswith('win'):
            self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        elif sys.platform.startswith('linux'):
            self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
            self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def _on_canvas_configure(self, event):
        """当画布大小改变时，调整内部 window item 的宽度，使其与画布宽度一致"""
        # 使用存储的 ID (self.window_item_id) 来配置宽度，避免 TclError
        self.canvas.itemconfig(self.window_item_id, width=event.width)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def get_frame(self):
        return self.scrollable_frame

# ==================== 主应用 ====================

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
        # 🚀 初始窗口大小调整
        root.geometry("800x900") 
        root.minsize(600, 400)
        root.resizable(True, True)
        root.configure(bg=Style.BG_LIGHT)

        self._load_icon(root)

        root.grid_rowconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)

        self._create_ui()
        self.setup_tray()
        self.load_autostart()
        
        # 默认启动监控（如果配置了开机自启）
        if self.config.get("autostart"):
             self.start()

    def _load_icon(self, root):
        """加载窗口图标"""
        try:
            if os.path.exists(ICON_ICO):
                root.iconbitmap(ICON_ICO)
            elif os.path.exists(ICON_PNG):
                self._icon_photo = tk.PhotoImage(file=ICON_PNG)
                root.iconphoto(False, self._icon_photo)
        except:
            pass

    def _create_ui(self):
        main_container = tk.Frame(self.root, bg=Style.BG_LIGHT)
        main_container.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        
        main_container.grid_rowconfigure(0, weight=0) # 头部
        main_container.grid_rowconfigure(1, weight=0) # 状态
        main_container.grid_rowconfigure(2, weight=0) # 按钮
        main_container.grid_rowconfigure(3, weight=1) # 底部空白
        main_container.grid_columnconfigure(0, weight=1)

        self._create_header(main_container)
        self._create_status_section(main_container)
        self._create_buttons(main_container)

    def _create_header(self, parent):
        header = tk.Frame(parent, bg=Style.BG_WHITE, height=80, relief="flat")
        header.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header.grid_columnconfigure(0, weight=1)
        header.pack_propagate(False)
        
        # 居中内容
        inner = tk.Frame(header, bg=Style.BG_WHITE)
        inner.pack(expand=True, padx=20)
        
        tk.Label(inner, text="🌐 AutoVPN 切换器", font=Style.FONT_TITLE, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(pady=(10, 0))
        tk.Label(inner, text="基于 WiFi 智能切换代理模式", font=Style.FONT_SUBTITLE, bg=Style.BG_WHITE, fg=Style.ACCENT_LIGHT).pack(pady=(0, 10))

    def _create_status_section(self, parent):
        # 状态卡片
        frame = tk.Frame(parent, bg=Style.BG_WHITE, highlightbackground="#dce0e6", highlightthickness=1, relief="flat")
        frame.grid(row=1, column=0, sticky="ew", padx=15, pady=(20, 15))
        frame.grid_columnconfigure(0, weight=1)
        
        inner = tk.Frame(frame, bg=Style.BG_WHITE)
        inner.pack(fill="x", expand=False, padx=20, pady=15)
        
        # 状态行
        status_row = tk.Frame(inner, bg=Style.BG_WHITE)
        status_row.pack(fill="x", padx=0, pady=(0, 10))
        
        tk.Label(status_row, text="运行状态:", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(side="left", padx=(0, 15))
        self.status_label = tk.Label(status_row, text="未启动", font=(Style.FONT_BODY[0], Style.FONT_BODY[1], "bold"), bg=Style.BG_WHITE, fg=Style.ACCENT_LIGHT)
        self.status_label.pack(side="left", fill="x", expand=True)
        
        # 模式行
        mode_row = tk.Frame(inner, bg=Style.BG_WHITE)
        mode_row.pack(fill="x", padx=0, pady=(0, 0))
        
        tk.Label(mode_row, text="当前模式:", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(side="left", padx=(0, 15))
        self.mode_label = tk.Label(mode_row, text="--", font=(Style.FONT_BODY[0], Style.FONT_BODY[1], "bold"), bg=Style.BG_WHITE, fg=Style.ACCENT_DARK)
        self.mode_label.pack(side="left", fill="x", expand=True)

    def _create_buttons(self, parent):
        frame = tk.Frame(parent, bg=Style.BG_LIGHT)
        frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(0, 15))
        
        btn_frame = tk.Frame(frame, bg=Style.BG_LIGHT)
        btn_frame.pack(fill="x")
        
        # 使用 grid 布局使按钮能自适应宽度
        btn_frame.grid_columnconfigure(0, weight=1)
        btn_frame.grid_columnconfigure(1, weight=1)
        btn_frame.grid_columnconfigure(2, weight=1)
        btn_frame.grid_columnconfigure(3, weight=1)
        
        self._create_styled_button(btn_frame, "▶ 启动", self.start, Style.SUCCESS, Style.PRIMARY_HOVER).grid(row=0, column=0, padx=4, pady=2, sticky="ew")
        self._create_styled_button(btn_frame, "⏹ 停止", self.stop, Style.ERROR, Style.PRIMARY_HOVER).grid(row=0, column=1, padx=4, pady=2, sticky="ew")
        self._create_styled_button(btn_frame, "⚙ 设置", self.open_settings, Style.PRIMARY, Style.PRIMARY_HOVER).grid(row=0, column=2, padx=4, pady=2, sticky="ew")
        self._create_styled_button(btn_frame, "📋 日志", self.open_log_window, Style.ACCENT_DARK, Style.PRIMARY_HOVER).grid(row=0, column=3, padx=4, pady=2, sticky="ew")

    def _create_styled_button(self, parent, text, command, bg_color, active_bg_color):
        """创建统一风格的按钮"""
        return tk.Button(parent, text=text, command=command, 
                         bg=bg_color, 
                         fg="white", 
                         relief="flat", 
                         activebackground=active_bg_color, # 悬停/点击反馈
                         activeforeground="white",
                         padx=10, 
                         pady=10, 
                         font=Style.FONT_BUTTON,
                         bd=0, # 无边框
                         cursor="hand2"
                         )

    def update_status(self, text, color):
        if self.status_label.winfo_exists():
            # 使用 after 确保线程安全地更新 UI
            self.root.after(0, lambda: self.status_label.config(text=f"⦿ {text}", fg=color))

    def update_mode(self, mode):
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
            args=(self.config["rules"], api, interval, self.log_box if (hasattr(self, 'log_box') and self.log_window and self.log_window.winfo_exists()) else None, self.stop_event, self.update_status, self.update_mode),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.update_status("停止中...", Style.WARNING) # 即时反馈
        
    def test_api_connection(self):
        """🚀 测试 Clash API 是否可达"""
        if not hasattr(self, 'test_api_label') or not self.test_api_label.winfo_exists():
             return # UI 元素不存在，退出

        api = self.e_api_settings.get().strip()
        if not api:
            self.test_api_label.config(text="❌ 请填写 API 地址", fg=Style.ERROR)
            return

        # 尝试连接 Clash /version 接口，因为它通常不需要认证
        # 兼容用户可能输入了 /configs 的情况
        test_url = api.split('/configs')[0].rstrip('/') + '/version' 
        
        # 线程化执行测试，避免 UI 假死
        def run_test():
            try:
                # 使用 GET，但只读取很小的部分
                req = urllib.request.Request(test_url, method="GET")
                req.add_header('User-Agent', 'AutoVPN-Switcher/1.0') 
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        self.root.after(0, lambda: self.test_api_label.config(text="✅ 连接成功！(HTTP 200)", fg=Style.SUCCESS))
                    else:
                        self.root.after(0, lambda: self.test_api_label.config(text=f"❌ 连接失败: HTTP {resp.status}", fg=Style.ERROR))
            except Exception as e:
                # 简单格式化错误信息
                err_msg = str(e).split(':')[-1].strip()
                self.root.after(0, lambda: self.test_api_label.config(text=f"❌ 连接失败: {err_msg}", fg=Style.ERROR))
                
        # Update label before starting thread
        self.test_api_label.config(text="正在测试...", fg=Style.WARNING)
        
        threading.Thread(target=run_test, daemon=True).start()


    def open_settings(self):
        """打开设置窗口 (应用新样式)"""
        settings_win = tk.Toplevel(self.root)
        settings_win.title("设置")
        # 🚀 初始窗口大小调整
        settings_win.geometry("700x900") 
        settings_win.minsize(600, 500)
        settings_win.transient(self.root)
        settings_win.grab_set()
        settings_win.configure(bg=Style.BG_LIGHT)

        # 主容器
        main = tk.Frame(settings_win, bg=Style.BG_LIGHT)
        main.pack(fill="both", expand=True)

        # 头部
        header = tk.Frame(main, bg=Style.BG_WHITE, height=60)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="⚙ 系统设置", font=("Microsoft YaHei UI", 14, "bold"), bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(pady=15)

        # 内容区 - 使用滚动框架
        scroll_frame = ScrollableFrame(main, bg=Style.BG_LIGHT)
        scroll_frame.pack(fill="both", expand=True, pady=10, padx=20)
        content = scroll_frame.get_frame()
        content.configure(bg=Style.BG_LIGHT)
        content.grid_columnconfigure(0, weight=1)

        # 规则部分
        rules_frame = tk.LabelFrame(content, text="多策略规则", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK, padx=15, pady=15, relief="flat", bd=1)
        rules_frame.pack(fill="x", pady=(10, 15), padx=0)

        self.rules_text_settings = scrolledtext.ScrolledText(rules_frame, height=6, font=Style.FONT_MONO, relief="flat", bd=0, bg=Style.BG_LIGHT, padx=10, pady=8, fg=Style.ACCENT_DARK)
        self.rules_text_settings.pack(fill="both", expand=True, pady=(0, 10))
        self._update_rules_text_settings()

        rules_btn = tk.Frame(rules_frame, bg=Style.BG_WHITE)
        rules_btn.pack(fill="x")
        self._create_styled_button(rules_btn, "添加规则", self.add_rule_settings, Style.PRIMARY, Style.PRIMARY_HOVER).pack(side="right", padx=(5, 0))
        self._create_styled_button(rules_btn, "清除规则", self.clear_rules_settings, Style.ACCENT_LIGHT, Style.PRIMARY_HOVER).pack(side="right", padx=5)

        # API 部分 (已修改)
        api_frame = tk.LabelFrame(content, text="Clash API 配置", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK, padx=15, pady=15, relief="flat", bd=1)
        api_frame.pack(fill="x", padx=0, pady=(0, 15))
        
        tk.Label(api_frame, text="API 地址:", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(anchor="w", pady=(0, 5), padx=10)
        
        # 容器用于 Entry 和 Button
        api_input_frame = tk.Frame(api_frame, bg=Style.BG_WHITE)
        api_input_frame.pack(fill="x", padx=10, pady=5)
        api_input_frame.grid_columnconfigure(0, weight=1)
        
        self.e_api_settings = tk.Entry(api_input_frame, font=Style.FONT_BODY, relief="flat", bd=1, bg=Style.BG_LIGHT, highlightthickness=1, highlightbackground="#ccc")
        self.e_api_settings.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.e_api_settings.insert(0, self.config["api_url"])
        
        # 🚀 测试按钮
        self.test_api_button = self._create_styled_button(api_input_frame, "测试连接", self.test_api_connection, Style.PRIMARY, Style.PRIMARY_HOVER)
        self.test_api_button.grid(row=0, column=1, sticky="e")
        
        # 🚀 结果标签
        self.test_api_label = tk.Label(api_frame, text="", font=Style.FONT_BODY, bg=Style.BG_WHITE)
        self.test_api_label.pack(anchor="w", padx=10, pady=(5, 0))


        # 间隔部分
        interval_frame = tk.LabelFrame(content, text="监控间隔", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK, padx=15, pady=15, relief="flat", bd=1)
        interval_frame.pack(fill="x", padx=0, pady=(0, 15))
        
        tk.Label(interval_frame, text="检查间隔 (秒):", font=Style.FONT_BODY, bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(anchor="w", pady=(0, 5))
        self.e_int_settings = tk.Entry(interval_frame, font=Style.FONT_BODY, relief="flat", bd=1, bg=Style.BG_LIGHT, highlightthickness=1, highlightbackground="#ccc", width=10)
        self.e_int_settings.pack(anchor="w", padx=10, pady=5)
        self.e_int_settings.insert(0, str(self.config["interval"]))

        # 开机启动
        autostart_frame = tk.Frame(content, bg=Style.BG_LIGHT)
        autostart_frame.pack(fill="x", padx=10, pady=10)
        v = tk.BooleanVar(value=self.config.get("autostart", False))
        chk = tk.Checkbutton(autostart_frame, text="开机启动", variable=v,
                             command=lambda: [self.config.update(autostart=v.get()), set_autostart(v.get()), save_config(self.config)],
                             bg=Style.BG_LIGHT, fg=Style.ACCENT_DARK, activebackground=Style.BG_LIGHT, selectcolor=Style.BG_WHITE, font=Style.FONT_BODY)
        chk.pack(anchor="w")

        # 底部保存按钮 - 固定在底部
        btn_frame = tk.Frame(settings_win, bg=Style.BG_LIGHT)
        btn_frame.pack(fill="x", padx=20, pady=(0, 20))
        self._create_styled_button(btn_frame, "保存所有设置并关闭", lambda: self.save_all_settings_settings(settings_win), Style.SUCCESS, Style.PRIMARY_HOVER).pack(fill="x")

    def _update_rules_text_settings(self):
        self.rules_text_settings.delete("1.0", tk.END)
        for r in self.config["rules"]:
            self.rules_text_settings.insert(tk.END, f"{r['ssids']} -> {r['mode']}\n")

    def clear_rules_settings(self):
        """清空规则文本框"""
        self.rules_text_settings.delete("1.0", tk.END)
        self.config["rules"] = []
        save_config(self.config)
        messagebox.showinfo("成功", "规则已清空。别忘了添加新规则并保存所有设置。")

    def add_rule_settings(self):
        """添加规则（设置窗口版本）"""
        win = tk.Toplevel(self.root)
        win.title("添加规则")
        # 🚀 初始窗口大小调整
        win.geometry("500x450")
        win.minsize(400, 350)
        win.transient(self.root)
        win.grab_set()
        win.configure(bg=Style.BG_LIGHT)

        main = tk.Frame(win, bg=Style.BG_LIGHT)
        main.pack(fill="both", expand=True)

        header = tk.Frame(main, bg=Style.BG_WHITE)
        header.pack(fill="x", padx=0, pady=0)
        tk.Label(header, text="➕ 添加规则", font=("Microsoft YaHei UI", 12, "bold"), bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(pady=10)

        content = tk.Frame(main, bg=Style.BG_LIGHT)
        content.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(content, text="WiFi名称 (逗号分隔):", font=Style.FONT_BODY, bg=Style.BG_LIGHT, fg=Style.ACCENT_DARK).pack(anchor="w", pady=(0, 5))
        e1 = tk.Entry(content, font=Style.FONT_BODY, relief="flat", bd=1, bg=Style.BG_WHITE, highlightthickness=1, highlightbackground="#ccc")
        e1.pack(fill="x", pady=(0, 15), padx=5)
        
        tk.Label(content, text="代理模式:", font=Style.FONT_BODY, bg=Style.BG_LIGHT, fg=Style.ACCENT_DARK).pack(anchor="w", pady=(0, 8))
        mode = tk.StringVar(value="Rule")
        
        modes = [("直连 (Direct)", "Direct"), ("规则 (Rule)", "Rule"), ("全局 (Global)", "Global")]
        for text, value in modes:
             tk.Radiobutton(content, text=text, variable=mode, value=value, font=Style.FONT_BODY, bg=Style.BG_LIGHT, fg=Style.ACCENT_DARK, activebackground=Style.BG_LIGHT, selectcolor=Style.BG_WHITE).pack(anchor="w", padx=5)

        btn_frame = tk.Frame(main, bg=Style.BG_LIGHT)
        btn_frame.pack(fill="x", padx=20, pady=20)

        def ok():
            ssids = e1.get().strip()
            if ssids:
                # 规则添加到倒数第二个位置，保证 * 规则在最后
                self.config["rules"].insert(len(self.config["rules"]) - 1 if self.config["rules"] and self.config["rules"][-1]["ssids"] == "*" else len(self.config["rules"]), 
                                            {"ssids": ssids, "mode": mode.get()})
                self._update_rules_text_settings()
                win.destroy()
            else:
                messagebox.showwarning("提示", "请输入WiFi名称")
        
        self._create_styled_button(btn_frame, "确定添加", ok, Style.SUCCESS, Style.PRIMARY_HOVER).pack(fill="x", side="left", padx=(0, 5))
        self._create_styled_button(btn_frame, "取消", win.destroy, Style.ACCENT_LIGHT, Style.PRIMARY_HOVER).pack(fill="x", side="left", padx=5)

    def save_rules_settings(self):
        """保存规则（设置窗口版本）"""
        try:
            text = self.rules_text_settings.get("1.0", tk.END).strip()
            rules = []
            has_default_rule = False
            for line in text.splitlines():
                if "->" in line:
                    ssids, mode = line.split("->", 1)
                    ssids = ssids.strip()
                    mode = mode.strip()
                    if ssids == "*":
                         has_default_rule = True
                    rules.append({"ssids": ssids, "mode": mode})
            
            # 确保至少有一个默认规则
            if not has_default_rule:
                 rules.append({"ssids": "*", "mode": "Rule"})
                 
            self.config["rules"] = rules
            # 注意: 不在此处弹出提示，由 save_all 统一处理
            return True
        except Exception as e:
            messagebox.showerror("错误", f"规则格式错误: 请检查行格式为 'WiFi名称,WiFi名称 -> Mode'。错误信息: {e}")
            return False

    def save_all_settings_settings(self, parent):
        """保存所有设置"""
        if not self.save_rules_settings():
             return # 规则保存失败则停止

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
            save_config(self.config)
            messagebox.showinfo("成功", "所有设置已保存")
            
            # 如果监控正在运行，提示用户重启以应用新间隔
            if self.thread and self.thread.is_alive() and self.stop_event.is_set() == False:
                 self.stop()
                 messagebox.showwarning("提示", "请点击'启动'按钮重启监控以应用新的 API 地址和检查间隔。")
                 
            parent.destroy()
        except Exception as e:
            messagebox.showerror("错误", f"保存失败: {e}")

    def open_log_window(self):
        """打开日志窗口 (应用新样式)"""
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            return

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("日志")
        # 🚀 初始窗口大小调整
        self.log_window.geometry("900x700")
        self.log_window.minsize(600, 400)
        self.log_window.protocol("WM_DELETE_WINDOW", self.close_log_window)
        self.log_window.configure(bg=Style.BG_LIGHT)

        main = tk.Frame(self.log_window, bg=Style.BG_LIGHT)
        main.pack(fill="both", expand=True, padx=0, pady=0)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        # 头部
        header = tk.Frame(main, bg=Style.BG_WHITE, height=50)
        header.grid(row=0, column=0, sticky="ew")
        header.pack_propagate(False)
        tk.Label(header, text="📋 运行日志", font=("Microsoft YaHei UI", 14, "bold"), bg=Style.BG_WHITE, fg=Style.ACCENT_DARK).pack(pady=12)

        # 日志框
        log_frame = tk.Frame(main, bg=Style.BG_WHITE)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=(10, 10))
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)

        # 专业的暗色日志框
        self.log_box = scrolledtext.ScrolledText(log_frame, font=Style.FONT_MONO, relief="flat", bd=0, 
                                                 bg="#2c3e50", fg="#ecf0f1", insertbackground="#ecf0f1", padx=10, pady=8)
        self.log_box.grid(row=0, column=0, sticky="nsew")

        # 底部按钮
        btn_frame = tk.Frame(main, bg=Style.BG_LIGHT)
        btn_frame.grid(row=2, column=0, sticky="ew", padx=15, pady=(0, 15))
        btn_frame.grid_columnconfigure(0, weight=1)
        
        btn_inner = tk.Frame(btn_frame, bg=Style.BG_LIGHT)
        btn_inner.pack(pady=5)
        
        self._create_styled_button(btn_inner, "清空日志", self.clear_log, Style.ERROR, Style.PRIMARY_HOVER).pack(side="left", padx=5)
        self._create_styled_button(btn_inner, "关闭窗口", self.close_log_window, Style.ACCENT_LIGHT, Style.PRIMARY_HOVER).pack(side="left", padx=5)

        log(f"配置加载: {CONFIG_FILE}", self.log_box)

    def close_log_window(self):
        """关闭日志窗口"""
        if self.log_window:
            self.log_window.destroy()
            self.log_window = None
            if hasattr(self, 'log_box'):
                 delattr(self, 'log_box')

    def clear_log(self):
        """清空日志"""
        if self.log_box and self.log_box.winfo_exists():
            self.log_box.delete("1.0", tk.END)
            try:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.truncate(0)
            except:
                pass
            log("日志文件已清空。", self.log_box)

    def setup_tray(self):
        # 托盘图标：优先使用 ico，然后 png，最后回退为简易生成图
        image = None
        if os.path.exists(ICON_ICO):
            try:
                image = Image.open(ICON_ICO)
            except: pass
        elif os.path.exists(ICON_PNG):
            try:
                image = Image.open(ICON_PNG)
            except: pass
        
        if image is None:
            # 简化生成的默认图标，使用 Style.PRIMARY
            image = Image.new("RGB", (64, 64), Style.PRIMARY)
            d = ImageDraw.Draw(image)
            try:
                # 尝试加载 Segoe UI Emoji 字体，以保证盾牌图标正确显示
                font = ImageFont.truetype("seguiemj.ttf", 40)
            except:
                font = ImageFont.load_default()
            d.text((10, 10), "🛡️", fill="white", font=font)

        menu = pystray.Menu(
            pystray.MenuItem("显示", self.show_window, default=True),
            pystray.MenuItem("退出", self.quit_app)
        )
        self.icon = pystray.Icon("AutoVPN", image, "VPN 切换器", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon=None, item=None):
        self.root.after(0, lambda: [self.root.deiconify(), self.root.lift()])

    def quit_app(self, icon=None, item=None):
        self.stop_event.set()
        
        def safe_exit():
            # 确保所有资源被释放
            try:
                if self.thread and self.thread.is_alive():
                    self.thread.join(timeout=2)
            except: pass
            
            try:
                if getattr(self, 'icon', None):
                    self.icon.stop()
            except: pass
            
            try:
                release_mutex()
            except: pass
            
            try:
                self.root.quit()
                self.root.destroy()
            except: pass
            
            try:
                # 最终确保进程退出
                os._exit(0)
            except: pass

        # 放在 after 中，确保在 tkinter 主循环中执行
        self.root.after(0, safe_exit)

    def on_close(self):
        self.quit_app()

    def load_autostart(self):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
            winreg.QueryValueEx(key, "AutoVPN")
            self.config["autostart"] = True
        except:
            self.config["autostart"] = False

# ==================== 主程序 ====================
if __name__ == "__main__":
    if not acquire_mutex("AutoVPN_SingleInstance_Mutex"):
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, "程序已在运行中", "AutoVPN 切换器", 0)
        else:
             print("程序已在运行中")
        sys.exit(0)

    # 尝试设置 DPI 缩放以在高分屏上显示正常
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
        
    root = tk.Tk()
    app = App(root)
    # 点击 X 时改为退出程序，确保后台线程和锁被正确清理
    root.protocol("WM_DELETE_WINDOW", app.quit_app) 
    
    try:
        root.mainloop()
    finally:
        # 确保退出时释放互斥（保险起见）
        try:
            release_mutex()
        except:
            pass
