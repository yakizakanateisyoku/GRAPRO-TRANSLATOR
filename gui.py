"""
OBS Chat Translator - tkinter GUI
起動: python gui.py
exe: pyinstaller --onefile --windowed --name obs-translator gui.py
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import webbrowser
import sys
import os

# main.pyと同じディレクトリをパスに追加（PyInstaller対応）
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import main as translator

# Flaskをバックグラウンドスレッドで起動
def start_flask():
    from flask import cli
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)  # Flaskログを抑制
    translator.start_workers()
    translator.app.run(
        host='127.0.0.1',
        port=translator.OVERLAY_PORT,
        debug=False,
        use_reloader=False,
    )

flask_thread = threading.Thread(target=start_flask, daemon=True)
flask_thread.start()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OBS Chat Translator")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ─── スタイル ───
        style = ttk.Style(self)
        style.theme_use('clam')

        BG   = "#1e1e2e"
        FG   = "#cdd6f4"
        ACC  = "#89b4fa"
        BTN_G = "#a6e3a1"
        BTN_R = "#f38ba8"
        self.configure(bg=BG)

        style.configure("TFrame",       background=BG)
        style.configure("TLabel",       background=BG, foreground=FG, font=("Meiryo UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=ACC, font=("Meiryo UI", 13, "bold"))
        style.configure("Status.TLabel",background=BG, foreground=FG,  font=("Meiryo UI",  9))
        style.configure("URL.TLabel",   background=BG, foreground=ACC, font=("Consolas",   9))
        style.configure("Start.TButton",font=("Meiryo UI", 10, "bold"), foreground="#1e1e2e", background=BTN_G)
        style.configure("Stop.TButton", font=("Meiryo UI", 10, "bold"), foreground="#1e1e2e", background=BTN_R)
        style.configure("TEntry",       fieldbackground="#313244", foreground=FG, insertcolor=FG)

        pad = dict(padx=14, pady=6)

        # ─── タイトル ───
        ttk.Label(self, text="🎌  OBS Chat Translator", style="Title.TLabel").pack(padx=14, pady=(14,2))
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10)

        # ─── Video ID 入力 ───
        f_vid = ttk.Frame(self)
        f_vid.pack(**pad, fill="x")
        ttk.Label(f_vid, text="YouTube Video ID:").pack(side="left")
        self.entry_vid = ttk.Entry(f_vid, width=26)
        self.entry_vid.pack(side="left", padx=(8,0))
        self.entry_vid.bind("<Return>", lambda e: self._start())

        # ─── ボタン ───
        f_btn = ttk.Frame(self)
        f_btn.pack(pady=(2,8))
        self.btn_start = ttk.Button(f_btn, text="▶  開始", style="Start.TButton", width=10, command=self._start)
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop  = ttk.Button(f_btn, text="■  停止", style="Stop.TButton",  width=10, command=self._stop,  state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10)

        # ─── ステータス表示 ───
        f_stat = ttk.Frame(self)
        f_stat.pack(**pad, fill="x")

        self.lbl_status  = ttk.Label(f_stat, text="状態: 待機中",    style="Status.TLabel")
        self.lbl_count   = ttk.Label(f_stat, text="翻訳件数: 0 件",  style="Status.TLabel")
        self.lbl_queue   = ttk.Label(f_stat, text="キュー: 0",       style="Status.TLabel")
        self.lbl_lt      = ttk.Label(f_stat, text="翻訳API: 確認中…", style="Status.TLabel")
        for lbl in (self.lbl_status, self.lbl_count, self.lbl_queue, self.lbl_lt):
            lbl.pack(anchor="w", pady=1)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=(4,0))

        # ─── OBS URL ───
        f_url = ttk.Frame(self)
        f_url.pack(**pad, fill="x")
        ttk.Label(f_url, text="OBS ブラウザソース URL:").pack(anchor="w")
        self.obs_url = f"http://localhost:{translator.OVERLAY_PORT}/"
        ttk.Label(f_url, text=self.obs_url, style="URL.TLabel").pack(anchor="w")

        f_url2 = ttk.Frame(self)
        f_url2.pack(pady=(0,12))
        ttk.Button(f_url2, text="URLをコピー",   command=self._copy_url).pack(side="left", padx=6)
        ttk.Button(f_url2, text="ブラウザで開く", command=self._open_browser).pack(side="left", padx=6)

        # ─── 定期更新スタート ───
        self._check_lt_api()
        self._poll_status()

    # ───────── コールバック ─────────

    def _start(self):
        vid = self.entry_vid.get().strip()
        if not vid:
            messagebox.showwarning("入力エラー", "YouTube Video ID を入力してください。")
            return
        # URLから抽出（貼り付けにも対応）
        if "v=" in vid:
            vid = vid.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in vid:
            vid = vid.split("youtu.be/")[-1].split("?")[0]

        import requests
        try:
            requests.get(f"http://localhost:{translator.OVERLAY_PORT}/start/{vid}", timeout=3)
        except Exception:
            pass
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.lbl_status.config(text=f"状態: 🟢 配信中  ({vid})")

    def _stop(self):
        import requests
        try:
            requests.get(f"http://localhost:{translator.OVERLAY_PORT}/stop", timeout=3)
        except Exception:
            pass
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="状態: ⚪ 停止済み")
        self.lbl_count.config(text="翻訳件数: 0 件")

    def _copy_url(self):
        self.clipboard_clear()
        self.clipboard_append(self.obs_url)
        messagebox.showinfo("コピー完了", f"クリップボードにコピーしました:\n{self.obs_url}")

    def _open_browser(self):
        webbrowser.open(self.obs_url)

    def _on_close(self):
        if messagebox.askokcancel("終了確認", "OBS翻訳ツールを終了しますか？\n（翻訳が停止します）"):
            self.destroy()
            os._exit(0)

    # ───────── 定期ポーリング ─────────

    def _poll_status(self):
        """1秒ごとにステータスを更新"""
        import requests
        try:
            r = requests.get(f"http://localhost:{translator.OVERLAY_PORT}/status", timeout=1)
            if r.status_code == 200:
                d = r.json()
                count = d.get("message_count", 0)
                queue = d.get("queue_size", 0)
                self.lbl_count.config(text=f"翻訳件数: {count} 件")
                self.lbl_queue.config(text=f"キュー: {queue}")
        except Exception:
            pass
        self.after(1000, self._poll_status)

    def _check_lt_api(self):
        """30秒ごとにLibreTranslate疎通確認"""
        def _check():
            import requests
            try:
                r = requests.get(f"http://localhost:{translator.OVERLAY_PORT}/lt_check", timeout=6)
                if r.status_code == 200 and r.json().get("status") == "ok":
                    self.lbl_lt.config(text="翻訳API: ✅ 接続OK")
                else:
                    self.lbl_lt.config(text="翻訳API: ⚠️ エラー")
            except Exception:
                self.lbl_lt.config(text="翻訳API: ❌ 接続失敗")
        threading.Thread(target=_check, daemon=True).start()
        self.after(30000, self._check_lt_api)


# ───────── エントリポイント ─────────
if __name__ == "__main__":
    # Flaskが立ち上がるまで少し待つ
    time.sleep(1.2)
    app = App()
    app.mainloop()
