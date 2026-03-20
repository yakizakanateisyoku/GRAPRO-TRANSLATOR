"""
OBS Chat Translator - tkinter GUI
起動: python gui.py  /  pythonw gui.py
exe: pyinstaller --onefile --windowed --name obs-translator gui.py
"""
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import webbrowser
import sys
import os
import requests

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import main as translator

# ── Flask をバックグラウンドで起動 ──────────────────────────
def _start_flask():
    import logging
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    translator.start_workers()
    translator.app.run(host='127.0.0.1', port=translator.OVERLAY_PORT,
                       debug=False, use_reloader=False)

threading.Thread(target=_start_flask, daemon=True).start()

# ── カラーパレット ───────────────────────────────────────────
BG      = "#1a1a2e"   # 背景
CARD    = "#16213e"   # カード背景
BORDER  = "#0f3460"   # 枠線
ACC     = "#e94560"   # アクセント（赤）
FG      = "#eaeaea"   # 通常テキスト
FG2     = "#a0a0b0"   # サブテキスト
BTN_G   = "#1a936f"   # 開始ボタン（緑）
BTN_R   = "#c0392b"   # 停止ボタン（赤）
BTN_N   = "#2c3e6b"   # テストボタン（ニュートラル）
ENTRY   = "#0d1b3e"   # 入力欄背景

PORT = translator.OVERLAY_PORT
OBS_URL = f"http://localhost:{PORT}/"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OBS Chat Translator")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll_status()
        self._check_lt()

    # ────────────────── UI構築 ──────────────────────────────
    def _build_ui(self):
        root_pad = dict(padx=16)

        # ── ヘッダー（タイトルバー風）──
        hdr = tk.Frame(self, bg=CARD, pady=10)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Live Chat Translator", bg=CARD, fg=FG,
                 font=("Segoe UI", 13, "bold")).pack()

        # ── ステータス行 ──
        sf = tk.Frame(self, bg=BG)
        sf.pack(fill="x", padx=16, pady=(12, 4))
        self.dot = tk.Label(sf, text="●", bg=BG, fg="#555", font=("Segoe UI", 10))
        self.dot.pack(side="left")
        self.lbl_status = tk.Label(sf, text=" 待機中", bg=BG, fg=FG2,
                                   font=("Segoe UI", 10))
        self.lbl_status.pack(side="left")
        self.lbl_count = tk.Label(sf, text="翻訳数: 0", bg=BG, fg=FG2,
                                  font=("Segoe UI", 10))
        self.lbl_count.pack(side="right")

        # ── Video ID 入力 ──
        tk.Label(self, text="YouTube URL または動画ID", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(**root_pad, anchor="w", pady=(8,2))

        self.entry_vid = tk.Entry(self, bg=ENTRY, fg=FG, insertbackground=FG,
                                  relief="flat", font=("Segoe UI", 11), bd=6)
        self.entry_vid.pack(fill="x", padx=16)
        self._placeholder("https://youtube.com/watch?v=...")
        self.entry_vid.bind("<Return>", lambda e: self._start())
        self.entry_vid.bind("<FocusIn>",  self._clear_ph)
        self.entry_vid.bind("<FocusOut>", self._restore_ph)

        # ── ボタン行 ──
        bf = tk.Frame(self, bg=BG)
        bf.pack(fill="x", padx=16, pady=(10, 4))

        self.btn_start = tk.Button(bf, text="開始", bg=BTN_G, fg="white",
            activebackground="#13704f", relief="flat", font=("Segoe UI", 10, "bold"),
            padx=0, pady=6, cursor="hand2", command=self._start)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0,4))

        self.btn_stop = tk.Button(bf, text="停止", bg=BTN_R, fg="white",
            activebackground="#922b21", relief="flat", font=("Segoe UI", 10, "bold"),
            padx=0, pady=6, cursor="hand2", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(0,4))

        tk.Button(bf, text="テスト", bg=BTN_N, fg=FG,
            activebackground="#3a4f8a", relief="flat", font=("Segoe UI", 10),
            padx=0, pady=6, cursor="hand2", command=self._test
        ).pack(side="left", fill="x", expand=True)

        # ── OBS URL カード ──
        url_card = tk.Frame(self, bg=CARD, bd=0, relief="flat")
        url_card.pack(fill="x", padx=16, pady=(10, 4))

        inner = tk.Frame(url_card, bg=CARD, padx=10, pady=8)
        inner.pack(fill="x")
        tk.Label(inner, text="OBS ブラウザソース URL", bg=CARD, fg=FG2,
                 font=("Segoe UI", 9)).pack(anchor="w")

        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(3,0))
        tk.Label(row, text=OBS_URL, bg=CARD, fg=ACC,
                 font=("Consolas", 10)).pack(side="left")
        tk.Button(row, text="コピー", bg=BORDER, fg=FG, relief="flat",
                  font=("Segoe UI", 9), padx=8, pady=2, cursor="hand2",
                  command=self._copy_url).pack(side="right")

        # ── 直近のメッセージ ──
        tk.Label(self, text="直近のメッセージ", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(padx=16, anchor="w", pady=(10,2))

        msg_frame = tk.Frame(self, bg=CARD, bd=0)
        msg_frame.pack(fill="x", padx=16, pady=(0,12))

        self.msg_canvas = tk.Frame(msg_frame, bg=CARD, padx=8, pady=8)
        self.msg_canvas.pack(fill="x")
        self.lbl_no_msg = tk.Label(self.msg_canvas, text="開始するとここに表示されます",
                                   bg=CARD, fg=FG2, font=("Segoe UI", 9))
        self.lbl_no_msg.pack()
        self.msg_labels = []

        # ── LT状態（フッター）──
        self.lbl_lt = tk.Label(self, text="翻訳API: 確認中…", bg=BG, fg=FG2,
                               font=("Segoe UI", 8))
        self.lbl_lt.pack(pady=(0,8))

    # ────────────────── プレースホルダー ────────────────────
    def _placeholder(self, text):
        self._ph_text = text
        self._ph_active = True
        self.entry_vid.insert(0, text)
        self.entry_vid.config(fg="#555")

    def _clear_ph(self, _=None):
        if self._ph_active:
            self.entry_vid.delete(0, "end")
            self.entry_vid.config(fg=FG)
            self._ph_active = False

    def _restore_ph(self, _=None):
        if not self.entry_vid.get().strip():
            self._ph_active = True
            self.entry_vid.insert(0, self._ph_text)
            self.entry_vid.config(fg="#555")

    # ────────────────── ボタンコールバック ──────────────────
    def _get_vid(self):
        raw = self.entry_vid.get().strip()
        if self._ph_active or not raw:
            return None
        if "v=" in raw:   raw = raw.split("v=")[-1].split("&")[0]
        elif "youtu.be/" in raw: raw = raw.split("youtu.be/")[-1].split("?")[0]
        return raw

    def _start(self):
        vid = self._get_vid()
        if not vid:
            messagebox.showwarning("入力エラー", "YouTube Video ID / URL を入力してください。")
            return
        try: requests.get(f"http://localhost:{PORT}/start/{vid}", timeout=3)
        except: pass
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.dot.config(fg="#1a936f")
        self.lbl_status.config(text=f" 配信中  ({vid[:20]})")

    def _stop(self):
        try: requests.get(f"http://localhost:{PORT}/stop", timeout=3)
        except: pass
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.dot.config(fg="#555")
        self.lbl_status.config(text=" 待機中")
        self.lbl_count.config(text="翻訳数: 0")
        self._update_msgs([])

    def _test(self):
        try: requests.get(f"http://localhost:{PORT}/test", timeout=3)
        except: pass

    def _copy_url(self):
        self.clipboard_clear(); self.clipboard_append(OBS_URL)

    # ────────────────── メッセージ更新 ──────────────────────
    def _update_msgs(self, msgs):
        for lbl in self.msg_labels:
            lbl.destroy()
        self.msg_labels = []

        if not msgs:
            self.lbl_no_msg.pack()
            return
        self.lbl_no_msg.pack_forget()

        for m in msgs[:4]:
            author = m.get("author", "")
            translated = m.get("translated")
            original  = m.get("original", "")
            lang = m.get("lang", "")
            text = translated if translated else original

            row = tk.Frame(self.msg_canvas, bg=CARD)
            row.pack(fill="x", pady=2)

            badge_color = ACC if translated else "#555"
            tk.Label(row, text=f"[{lang}]", bg=CARD, fg=badge_color,
                     font=("Consolas", 8)).pack(side="left")
            tk.Label(row, text=f" {author}: ", bg=CARD, fg=FG2,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=text[:40]+"…" if len(text)>40 else text,
                     bg=CARD, fg=FG, font=("Segoe UI", 9),
                     wraplength=260, anchor="w").pack(side="left")
            self.msg_labels.append(row)

    # ────────────────── ポーリング ───────────────────────────
    def _poll_status(self):
        def _do():
            try:
                r = requests.get(f"http://localhost:{PORT}/messages", timeout=1)
                if r.ok:
                    msgs = r.json()
                    count = len(msgs)
                    self.after(0, lambda: self.lbl_count.config(text=f"翻訳数: {count}"))
                    self.after(0, lambda: self._update_msgs(msgs))
            except: pass
        threading.Thread(target=_do, daemon=True).start()
        self.after(1500, self._poll_status)

    def _check_lt(self):
        def _do():
            try:
                r = requests.get(f"http://localhost:{PORT}/lt_check", timeout=6)
                ok = r.ok and r.json().get("status") == "ok"
                txt = "翻訳API: ✅ 接続OK" if ok else "翻訳API: ⚠️ エラー"
                col = "#1a936f" if ok else "#e94560"
            except:
                txt, col = "翻訳API: ❌ 接続失敗", "#e94560"
            self.after(0, lambda: self.lbl_lt.config(text=txt, fg=col))
        threading.Thread(target=_do, daemon=True).start()
        self.after(30000, self._check_lt)

    def _on_close(self):
        if messagebox.askokcancel("終了", "OBS翻訳ツールを終了しますか？"):
            self.destroy(); os._exit(0)


# ── エントリポイント ─────────────────────────────────────────
if __name__ == "__main__":
    time.sleep(1.2)
    App().mainloop()
