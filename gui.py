"""
OBS Chat Translator - GUI (customtkinter版)
ブラウザプレビュー完全一致
"""
import customtkinter as ctk
import tkinter as tk
import threading, time, sys, os, requests, webbrowser, io
from PIL import Image, ImageDraw

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import main as translator

# Flask をバックグラウンドで起動
def _start_flask():
    import logging; logging.getLogger('werkzeug').setLevel(logging.ERROR)
    translator.start_workers()
    translator.app.run(host='127.0.0.1', port=translator.OVERLAY_PORT,
                       debug=False, use_reloader=False)
threading.Thread(target=_start_flask, daemon=True).start()

# テーマ設定
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

PORT    = translator.OVERLAY_PORT
OBS_URL = f"http://localhost:{PORT}/"

# 色定数
CARD_BG  = "#ffffff"
CARD_BD  = "#e0dfd6"
BODY_BG  = "#f0efe8"
ENTRY_BG = "#f8f7f2"
ENTRY_BD = "#d8d7d0"
FG1      = "#1a1a1a"
FG2      = "#666666"
FG3      = "#aaaaaa"
ACC      = "#29b6f6"
BTN_G    = "#1a936f"
BTN_R    = "#c0392b"

# 接続を再利用してTIME_WAIT枯渇を防ぐ
SESSION = requests.Session()

# アバター画像キャッシュ
_avatar_cache = {}

def fetch_avatar(url, size=22):
    """URLから円形アバター画像を取得（キャッシュあり）"""
    if not url:
        return None
    if url in _avatar_cache:
        return _avatar_cache[url]
    try:
        resp = SESSION.get(url, timeout=3)
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        img = img.resize((size*2, size*2), Image.LANCZOS)
        # 円形マスク
        mask = Image.new("L", (size*2, size*2), 0)
        ImageDraw.Draw(mask).ellipse((0,0,size*2-1,size*2-1), fill=255)
        img.putalpha(mask)
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
        _avatar_cache[url] = ctk_img
        return ctk_img
    except:
        return None

LANG = {
    "en":"英語","ko":"韓国語","zh-cn":"中国語","zh":"中国語","zh-hans":"中国語(簡体)",
    "zh-tw":"中国語(繁体)","zh-hant":"中国語(繁体)","ru":"ロシア語","fr":"フランス語",
    "de":"ドイツ語","es":"スペイン語","pt":"ポルトガル語","ar":"アラビア語","ja":"日本語",
    "it":"イタリア語","nl":"オランダ語","pl":"ポーランド語","tr":"トルコ語","uk":"ウクライナ語",
    "vi":"ベトナム語","th":"タイ語","id":"インドネシア語","hi":"ヒンディー語",
}

class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color="#f0efe8")
        self.title("Live Chat Translator")
        self.resizable(False, False)
        self.geometry("360x600")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._msg_widgets = []
        self._last_msg_key = ""
        self._ph_active = True
        self._build()
        self._poll()
        self._check_lt()

    def _build(self):
        # ── メインカード ──
        card = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=10)
        card.pack(fill="both", expand=True, padx=12, pady=12)

        # ステータス行
        sf = ctk.CTkFrame(card, fg_color="transparent")
        sf.pack(fill="x", padx=14, pady=(12,0))
        self._dot = ctk.CTkLabel(sf, text="●", text_color="#bbbbbb",
                                  font=("Arial",11), fg_color="transparent")
        self._dot.pack(side="left")
        self._lbl_st = ctk.CTkLabel(sf, text=" 待機中", text_color="#888888",
                                     font=ctk.CTkFont("Yu Gothic UI",12),
                                     fg_color="transparent")
        self._lbl_st.pack(side="left")

        # 翻訳APIステータス丸（右上）
        api_frame = ctk.CTkFrame(sf, fg_color="transparent")
        api_frame.pack(side="right")
        ctk.CTkLabel(api_frame, text="翻訳API", text_color="#bbbbbb",
                     font=ctk.CTkFont("Yu Gothic UI",10),
                     fg_color="transparent").pack(side="left", padx=(0,4))
        self._api_dot = ctk.CTkLabel(api_frame, text="●", text_color="#dddddd",
                                      font=("Arial",12), fg_color="transparent")
        self._api_dot.pack(side="left")

        # 入力ラベル
        ctk.CTkLabel(card, text="YouTube 配信URL",
                     text_color="#888888", font=ctk.CTkFont("Yu Gothic UI",12),
                     fg_color="transparent").pack(anchor="w", padx=14, pady=(10,3))

        # 入力欄
        self._entry = ctk.CTkEntry(
            card, placeholder_text="https://youtube.com/watch?v=...",
            fg_color="#ffffff", border_color="#dddddd", border_width=1,
            text_color="#333333", placeholder_text_color="#bbbbbb",
            font=ctk.CTkFont("Yu Gothic UI",12), height=36, corner_radius=6)
        self._entry.pack(fill="x", padx=14, pady=(0,10))
        self._entry.bind("<Return>", lambda e: self._start())

        # ── ボタン行（2:2:1）──
        TOTAL = 360 - 28 - 28   # window - card_pad*2 - inner_pad*2
        GAP   = 4
        BW1   = (TOTAL - GAP*2) * 2 // 5   # 2/5
        BW2   = TOTAL - BW1*2 - GAP*2      # 1/5
        bf = ctk.CTkFrame(card, fg_color="transparent")
        bf.pack(fill="x", padx=14, pady=(0,10))

        self._btn_start = ctk.CTkButton(
            bf, text="開始", fg_color="#ffffff", hover_color="#f0faf5",
            text_color="#1a1a1a", font=ctk.CTkFont("Yu Gothic UI",13,"bold"),
            border_width=1, border_color="#cccccc",
            height=36, corner_radius=6, width=BW1, command=self._start)
        self._btn_start.pack(side="left", padx=(0,GAP))

        self._btn_stop = ctk.CTkButton(
            bf, text="停止", fg_color="#ffffff", hover_color="#fff0f0",
            text_color="#bbbbbb", font=ctk.CTkFont("Yu Gothic UI",13,"bold"),
            border_width=1, border_color="#dddddd",
            height=36, corner_radius=6, width=BW1, command=self._stop, state="disabled")
        self._btn_stop.pack(side="left", padx=(0,GAP))

        self._btn_test = ctk.CTkButton(
            bf, text="テスト", fg_color="#ffffff", hover_color="#f5f5f5",
            text_color="#444444", font=ctk.CTkFont("Yu Gothic UI",13),
            border_width=1, border_color="#dddddd",
            height=36, corner_radius=6, width=BW2, command=self._test)
        self._btn_test.pack(side="left")

        # ── OBS URL カード ──
        url_card = ctk.CTkFrame(card, fg_color="#f8f7f2",
                                corner_radius=6, border_width=1,
                                border_color="#e0dfd6")
        url_card.pack(fill="x", padx=14, pady=(0,10))
        ctk.CTkLabel(url_card, text="OBS ブラウザソース URL",
                     text_color="#888888", font=ctk.CTkFont("Yu Gothic UI",11),
                     fg_color="transparent").pack(anchor="w", padx=12, pady=(8,2))
        url_row = ctk.CTkFrame(url_card, fg_color="transparent")
        url_row.pack(fill="x", padx=12, pady=(0,8))
        ctk.CTkLabel(url_row, text=OBS_URL, text_color="#29b6f6",
                     font=ctk.CTkFont("Consolas",12),
                     fg_color="transparent").pack(side="left")
        self._btn_copy = ctk.CTkButton(
            url_row, text="開く", fg_color="#ffffff",
            hover_color="#f0efe8", text_color="#555555",
            font=ctk.CTkFont("Yu Gothic UI",11), border_width=1,
            border_color="#d0cfc8", height=26, width=62, corner_radius=5,
            command=self._open_url)
        self._btn_copy.pack(side="right")

        # ── 直近のメッセージ ──
        # 直近のメッセージ（クリックで折りたたみ）
        self._msg_visible = True
        msg_header = ctk.CTkFrame(card, fg_color="transparent")
        msg_header.pack(fill="x", padx=14, pady=(0,4))
        self._lbl_msg_toggle = ctk.CTkLabel(
            msg_header, text="直近のメッセージ  ▾",
            text_color="#888888", font=ctk.CTkFont("Yu Gothic UI",11),
            fg_color="transparent", cursor="hand2")
        self._lbl_msg_toggle.pack(side="left")
        self._lbl_msg_toggle.bind("<Button-1>", self._toggle_msgs)

        self._msg_frame = ctk.CTkFrame(card, fg_color="transparent", height=200)
        self._msg_frame.pack(fill="x", padx=14, pady=(0,6))
        self._msg_frame.pack_propagate(False)
        self._lbl_empty = ctk.CTkLabel(
            self._msg_frame, text="開始するとここに表示されます",
            text_color="#bbbbbb", font=ctk.CTkFont("Yu Gothic UI",10),
            fg_color="transparent")
        self._lbl_empty.pack(pady=10)

        # フッター
        # フッター（pack順序の基準点として使用）
        self._footer_frame = ctk.CTkFrame(card, fg_color="transparent", height=8)
        self._footer_frame.pack(pady=(4,8))

    # ── ボタンコールバック ──
    def _toggle_msgs(self, _=None):
        self._msg_visible = not self._msg_visible
        if self._msg_visible:
            # フッターの直前に挿入することで順序を維持
            self._msg_frame.pack(fill="x", padx=14, pady=(0,6),
                                 before=self._footer_frame)
            self._lbl_msg_toggle.configure(text="直近のメッセージ  ▾")
            self.geometry("360x600")
        else:
            self._msg_frame.pack_forget()
            self._lbl_msg_toggle.configure(text="直近のメッセージ  ▸")
            self.geometry("360x370")

    def _get_vid(self, raw):
        """URLまたはIDからvideo_idを抽出"""
        raw = raw.strip()
        if not raw: return None
        if "youtube.com" in raw or "youtu.be" in raw:
            if "v=" in raw:
                raw = raw.split("v=")[-1].split("&")[0]
            elif "youtu.be/" in raw:
                raw = raw.split("youtu.be/")[-1].split("?")[0]
        # 11文字以上の英数字ならvideo_idとみなす
        if len(raw) >= 8:
            return raw
        return None

    def _start(self):
        raw = self._entry.get().strip()
        vid = self._get_vid(raw)
        if not vid:
            self._lbl_st.configure(text=" URLを入力してください", text_color="#c0392b")
            return
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/start/{vid}", timeout=5)
                if r.ok:
                    self.after(0, lambda: self._lbl_st.configure(
                        text=f" 配信中 ({vid[:16]})", text_color="#1a936f"))
            except Exception as e:
                self.after(0, lambda: self._lbl_st.configure(
                    text=f" 接続エラー", text_color="#c0392b"))
        self._btn_start.configure(state="disabled", fg_color="#f5f5f5",
                                   text_color="#bbbbbb", border_color="#e0e0e0")
        self._btn_stop.configure(state="normal", fg_color="#fff0f0",
                                  hover_color="#ffe0e0", text_color="#c0392b",
                                  border_color="#f5a0a0")
        self._dot.configure(text_color="#1a936f")
        self._lbl_st.configure(text=f" 開始中…", text_color="#1a936f")
        threading.Thread(target=_do, daemon=True).start()

    def _stop(self):
        try: SESSION.get(f"http://localhost:{PORT}/stop", timeout=3)
        except: pass
        self._btn_start.configure(state="normal", fg_color="#ffffff",
                                   text_color="#1a1a1a", border_color="#cccccc")
        self._btn_stop.configure(state="disabled", fg_color="#ffffff",
                                  text_color="#bbbbbb", border_color="#dddddd")
        self._dot.configure(text_color="#bbbbbb")
        self._lbl_st.configure(text=" 待機中", text_color="#888888")
        self._render_msgs([])

    def _test(self):
        def _do():
            try:
                SESSION.get(f"http://localhost:{PORT}/test", timeout=3)
            except: pass
        threading.Thread(target=_do, daemon=True).start()
        self._btn_test.configure(text="✓ 注入済", fg_color="#e8f5e9", text_color="#1a936f")
        self.after(1500, lambda: self._btn_test.configure(
            text="テスト", fg_color="#ffffff", text_color="#444444"))

    def _open_url(self):
        webbrowser.open(OBS_URL)
        self._btn_copy.configure(text="✓ 開いた", text_color="#1a936f")
        self.after(1500, lambda: self._btn_copy.configure(text="開く", text_color="#555555"))

    # ── メッセージ描画（ブラウザプレビュー準拠）──
    def _render_msgs(self, msgs):
        # 差分がなければ再描画しない（ちらつき防止）
        key = "|".join(f"{m.get('author','')}:{m.get('original','')[:20]}" for m in msgs[:5])
        if key == self._last_msg_key:
            return
        self._last_msg_key = key

        for w in self._msg_widgets: w.destroy()
        self._msg_widgets = []
        if not msgs:
            self._lbl_empty.pack(pady=10); return
        self._lbl_empty.pack_forget()

        for m in msgs[:5]:
            lang       = m.get("lang","")
            lang_label = LANG.get(lang.lower(), lang)
            translated = m.get("translated")
            original   = m.get("original","")
            author     = m.get("author","")
            text       = translated if translated else original

            outer = tk.Frame(self._msg_frame, bg=CARD_BG)
            outer.pack(fill="x", pady=(0,8))

            # 左：アバター（28px丸）
            av_col = tk.Frame(outer, bg=CARD_BG, width=36)
            av_col.pack(side="left", anchor="n", padx=(0,8), pady=(2,0))
            av_col.pack_propagate(False)
            av_label = ctk.CTkLabel(av_col, text="", fg_color="transparent",
                                    width=28, height=28)
            av_label.pack()
            if m.get("imageUrl"):
                def _load_av(lbl=av_label, url=m["imageUrl"]):
                    img = fetch_avatar(url, size=28)
                    if img:
                        try: lbl.configure(image=img, text="")
                        except: pass
                threading.Thread(target=_load_av, daemon=True).start()

            # 右：名前・バッジ・テキスト
            right = tk.Frame(outer, bg=CARD_BG)
            right.pack(side="left", fill="x", expand=True)

            # 名前 + バッジ
            meta = tk.Frame(right, bg=CARD_BG)
            meta.pack(fill="x")
            ctk.CTkLabel(meta, text=author, text_color="#333333",
                         font=ctk.CTkFont("Yu Gothic UI",11,"bold"),
                         fg_color="transparent").pack(side="left")
            if m.get("badgeUrl"):
                bl = ctk.CTkLabel(meta, text="", fg_color="transparent",
                                  width=16, height=16)
                bl.pack(side="left", padx=(4,0))
                def _load_b(lbl=bl, url=m["badgeUrl"]):
                    img = fetch_avatar(url, size=16)
                    if img:
                        try: lbl.configure(image=img)
                        except: pass
                threading.Thread(target=_load_b, daemon=True).start()
            elif m.get("isOwner"):
                ctk.CTkLabel(meta, text=" 配信者 ", fg_color="#f1c40f",
                             text_color="#000", font=ctk.CTkFont("Yu Gothic UI",8,"bold"),
                             corner_radius=3).pack(side="left", padx=(4,0))
            elif m.get("isMod"):
                ctk.CTkLabel(meta, text=" モデ ", fg_color="#5865f2",
                             text_color="#fff", font=ctk.CTkFont("Yu Gothic UI",8,"bold"),
                             corner_radius=3).pack(side="left", padx=(4,0))
            if m.get("isMember") and not m.get("badgeUrl"):
                ctk.CTkLabel(meta, text=" メンバー ", fg_color="#2ecc71",
                             text_color="#000", font=ctk.CTkFont("Yu Gothic UI",8,"bold"),
                             corner_radius=3).pack(side="left", padx=(4,0))

            # 言語バッジ（翻訳時のみ）+ テキスト
            txt_row = tk.Frame(right, bg=CARD_BG)
            txt_row.pack(fill="x", pady=(1,0))
            if translated:
                ctk.CTkLabel(txt_row, text=f" {lang_label} ",
                             fg_color=ACC, text_color="#000",
                             font=ctk.CTkFont("Yu Gothic UI",9,"bold"),
                             corner_radius=4).pack(side="left", padx=(0,5))
            ctk.CTkLabel(txt_row, text=text, text_color=FG1,
                         font=ctk.CTkFont("Yu Gothic UI",13), fg_color="transparent",
                         anchor="w", justify="left", wraplength=255).pack(side="left")

            # 原文（翻訳時のみ・薄グレー）
            if translated:
                ctk.CTkLabel(right, text=original, text_color=FG3,
                             font=ctk.CTkFont("Yu Gothic UI",10), fg_color="transparent",
                             anchor="w", justify="left", wraplength=280).pack(fill="x")

            self._msg_widgets.append(outer)

    # ── ポーリング ──
    def _poll(self):
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/messages", timeout=1)
                if r.ok:
                    msgs = r.json()
                    self.after(0, lambda: self._render_msgs(msgs))
            except: pass
        threading.Thread(target=_do, daemon=True).start()
        self.after(800, self._poll)

    def _check_lt(self):
        """翻訳API状態確認: 緑=OK / 黄=確認中 / 赤=エラー"""
        self.after(0, lambda: self._api_dot.configure(text_color="#f1c40f"))  # 確認中=黄
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/lt_check", timeout=8)
                ok = r.ok and r.json().get("status") == "ok"
                color = "#2ecc71" if ok else "#e74c3c"
            except:
                color = "#e74c3c"
            self.after(0, lambda: self._api_dot.configure(text_color=color))
        threading.Thread(target=_do, daemon=True).start()
        self.after(30000, self._check_lt)

    def _on_close(self):
        import tkinter.messagebox as mb
        if mb.askokcancel("終了", "OBS翻訳ツールを終了しますか？"):
            self.destroy(); os._exit(0)


if __name__ == "__main__":
    time.sleep(1.2)
    App().mainloop()
