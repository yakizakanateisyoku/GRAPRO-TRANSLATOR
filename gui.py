"""
OBS Chat Translator - GUI (customtkinter版)
v1.0.0 - UI整理版
"""
import customtkinter as ctk
import tkinter as tk
import threading, time, sys, os, requests, webbrowser, io
from PIL import Image, ImageDraw
import stats

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
ACC      = "#29b6f6"

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
        self.title(f"Live Chat Translator v{translator.VERSION}")
        self.resizable(False, True)
        self.geometry("360x520")
        self.minsize(360, 400)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._slots = []
        self._slot_keys = []
        self._streaming = False
        self._build()
        self._poll()
        self._check_lt()

    def _build(self):
        # ── メインカード ──
        card = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=10)
        card.pack(fill="both", expand=True, padx=12, pady=12)
        card.pack_propagate(False)
        self._card = card

        # ステータス行
        sf = ctk.CTkFrame(card, fg_color="transparent")
        sf.pack(fill="x", padx=14, pady=(12,0))
        self._dot = ctk.CTkLabel(sf, text="●", text_color="#bbbbbb",
                                  font=("Arial",11), fg_color="transparent")
        self._dot.pack(side="left")
        self._lbl_st = ctk.CTkLabel(sf, text=" 待機中", text_color="#888888",
                                     font=ctk.CTkFont("Meiryo",12),
                                     fg_color="transparent")
        self._lbl_st.pack(side="left")

        # 翻訳APIステータス + 歯車（右上）
        api_frame = ctk.CTkFrame(sf, fg_color="transparent")
        api_frame.pack(side="right")
        ctk.CTkLabel(api_frame, text="翻訳API", text_color="#bbbbbb",
                     font=ctk.CTkFont("Meiryo",10),
                     fg_color="transparent").pack(side="left", padx=(0,4))
        self._api_dot = ctk.CTkLabel(api_frame, text="●", text_color="#dddddd",
                                      font=("Arial",12), fg_color="transparent")
        self._api_dot.pack(side="left")
        self._api_gear = ctk.CTkLabel(api_frame, text="⚙", text_color="#aaaaaa",
                                       font=("Arial",14), fg_color="transparent",
                                       cursor="hand2")
        self._api_gear.pack(side="left", padx=(4,0))
        self._api_gear.bind("<Button-1>", self._open_api_settings)

        # 入力ラベル
        ctk.CTkLabel(card, text="YouTube 配信URL",
                     text_color="#888888", font=ctk.CTkFont("Meiryo",12),
                     fg_color="transparent").pack(anchor="w", padx=14, pady=(10,3))

        # 入力欄
        self._entry = ctk.CTkEntry(
            card, placeholder_text="https://youtube.com/watch?v=...",
            fg_color="#ffffff", border_color="#dddddd", border_width=1,
            text_color="#333333", placeholder_text_color="#bbbbbb",
            font=ctk.CTkFont("Meiryo",12), height=36, corner_radius=6)
        self._entry.pack(fill="x", padx=14, pady=(0,10))
        self._entry.bind("<Return>", lambda e: self._start())

        # ── ボタン行（開始/停止 均等2列）──
        bf = ctk.CTkFrame(card, fg_color="transparent")
        bf.pack(fill="x", padx=14, pady=(0,10))

        self._btn_start = ctk.CTkButton(
            bf, text="開始", fg_color="#ffffff", hover_color="#f0faf5",
            text_color="#1a1a1a", font=ctk.CTkFont("Meiryo",13,"bold"),
            border_width=1, border_color="#cccccc",
            height=36, corner_radius=6, command=self._start)
        self._btn_start.pack(side="left", fill="x", expand=True, padx=(0,2))

        self._btn_stop = ctk.CTkButton(
            bf, text="停止", fg_color="#ffffff", hover_color="#fff0f0",
            text_color="#bbbbbb", font=ctk.CTkFont("Meiryo",13,"bold"),
            border_width=1, border_color="#dddddd",
            height=36, corner_radius=6, command=self._stop, state="disabled")
        self._btn_stop.pack(side="left", fill="x", expand=True, padx=(2,0))

        # ── OBS URL コンパクト行 ──
        obs_row = ctk.CTkFrame(card, fg_color="transparent")
        obs_row.pack(fill="x", padx=14, pady=(0,10))
        ctk.CTkLabel(obs_row, text="OBS:", text_color="#888888",
                     font=ctk.CTkFont("Meiryo",11),
                     fg_color="transparent").pack(side="left")
        ctk.CTkLabel(obs_row, text=f"localhost:{PORT}",
                     text_color="#29b6f6", font=ctk.CTkFont("Consolas",11),
                     fg_color="transparent").pack(side="left", padx=(4,0))
        self._btn_copy = ctk.CTkButton(
            obs_row, text="コピー", fg_color="#ffffff",
            hover_color="#f0efe8", text_color="#555555",
            font=ctk.CTkFont("Meiryo",10), border_width=1,
            border_color="#d0cfc8", height=24, width=50, corner_radius=4,
            command=self._copy_url)
        self._btn_copy.pack(side="right")


        # ── 翻訳済みメッセージ ──
        ctk.CTkLabel(card, text="翻訳済みメッセージ",
                     text_color="#888888", font=ctk.CTkFont("Meiryo",11),
                     fg_color="transparent").pack(anchor="w", padx=14, pady=(0,4))

        self._msg_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._msg_frame.pack(fill="both", expand=True, padx=14, pady=(0,12))

        self._lbl_empty = ctk.CTkLabel(
            self._msg_frame, text="開始するとここに表示されます",
            text_color="#bbbbbb", font=ctk.CTkFont("Meiryo",10),
            fg_color="transparent")
        self._lbl_empty.pack(pady=30)

        # スロット永続生成（ダークカード）・初期5個、リサイズで増減
        self._MSG_BG = "#1a1a1a"
        self._SLOT_HEIGHT = 70  # 1スロットあたりの推定高さ(px)
        self._HEADER_HEIGHT = 200  # ヘッダー部分の推定高さ(px)
        for _ in range(5):
            self._create_slot()
        self.bind("<Configure>", self._on_resize)
        self._last_slot_count = 5

    def _create_slot(self):
        """メッセージ表示スロットを1つ追加"""
        BG = self._MSG_BG
        slot = {}
        f = tk.Frame(self._msg_frame, bg=BG, highlightthickness=0)
        border = tk.Frame(f, bg="#555555", width=4)
        border.pack(side="left", fill="y")
        slot["border"] = border
        inner = tk.Frame(f, bg=BG, padx=8, pady=6)
        inner.pack(side="left", fill="x", expand=True)
        meta = tk.Frame(inner, bg=BG)
        meta.pack(fill="x")
        av = ctk.CTkLabel(meta, text="", fg_color="transparent", width=22, height=22)
        av.pack(side="left", padx=(0,5))
        slot["avatar"] = av
        slot["_av_url"] = ""
        author = ctk.CTkLabel(meta, text="", text_color="#ffe066",
                               font=ctk.CTkFont("Meiryo",11,"bold"),
                               fg_color="transparent")
        author.pack(side="left")
        slot["author"] = author
        lang_b = ctk.CTkLabel(meta, text="", fg_color=ACC, text_color="#003",
                               font=ctk.CTkFont("Meiryo",9,"bold"),
                               corner_radius=4)
        slot["lang_badge"] = lang_b
        fb_btn = ctk.CTkLabel(meta, text="\U0001f44e", text_color="#666666",
                               font=("Arial",12), fg_color="transparent",
                               cursor="hand2")
        slot["fb_btn"] = fb_btn
        slot["_lang_code"] = ""
        text_lbl = ctk.CTkLabel(inner, text="", text_color="#ffffff",
                                 font=ctk.CTkFont("Meiryo",13),
                                 fg_color="transparent", anchor="w",
                                 justify="left", wraplength=280)
        text_lbl.pack(fill="x", pady=(2,0))
        slot["text"] = text_lbl
        orig_lbl = ctk.CTkLabel(inner, text="", text_color="#999999",
                                 font=ctk.CTkFont("Meiryo",10),
                                 fg_color="transparent", anchor="w",
                                 justify="left", wraplength=280)
        slot["original"] = orig_lbl
        slot["frame"] = f
        self._slots.append(slot)
        self._slot_keys.append(None)

    def _on_resize(self, event):
        """ウィンドウ縦リサイズ時にスロット数を調整"""
        if event.widget is not self:
            return
        h = event.height
        needed = max(3, (h - self._HEADER_HEIGHT) // self._SLOT_HEIGHT)
        if needed == self._last_slot_count:
            return
        while len(self._slots) < needed:
            self._create_slot()
        self._last_slot_count = needed

    # ── ボタンコールバック ──

    def _get_vid(self, raw):
        raw = raw.strip()
        if not raw: return None
        if "youtube.com" in raw or "youtu.be" in raw:
            if "v=" in raw:
                raw = raw.split("v=")[-1].split("&")[0]
            elif "youtu.be/" in raw:
                raw = raw.split("youtu.be/")[-1].split("?")[0]
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
                    if was_streaming:
                        self.after(0, lambda: self._flash_refresh())
            except:
                self.after(0, lambda: self._lbl_st.configure(
                    text=f" 接続エラー", text_color="#c0392b"))
        # 配信中なら「更新」として再接続
        was_streaming = self._streaming
        if was_streaming:
            self._retranslate()  # 未翻訳を再翻訳
        if was_streaming:
            self._btn_start.configure(text="再接続中…", state="disabled",
                                       text_color="#999999")
            self._dot.configure(text_color="#f1c40f")
            self._lbl_st.configure(text=" 再接続中…", text_color="#f1c40f")
        self._streaming = True
        self._entry.configure(state="disabled", fg_color="#f5f5f5", text_color="#999999")
        self._btn_start.configure(text="更新", fg_color="#f0faf5",
                                   hover_color="#e0f5eb",
                                   text_color="#1a936f", border_color="#a0d8c0")
        self._btn_stop.configure(state="normal", fg_color="#fff0f0",
                                  hover_color="#ffe0e0", text_color="#c0392b",
                                  border_color="#f5a0a0")
        self._dot.configure(text_color="#1a936f")
        self._lbl_st.configure(text=f" 開始中…", text_color="#1a936f")
        threading.Thread(target=_do, daemon=True).start()

    def _stop(self):
        try: SESSION.get(f"http://localhost:{PORT}/stop", timeout=3)
        except: pass
        self._streaming = False
        self._entry.configure(state="normal", fg_color="#ffffff", text_color="#333333")
        self._btn_start.configure(text="開始", fg_color="#ffffff",
                                   hover_color="#f0faf5",
                                   text_color="#1a1a1a", border_color="#cccccc")
        self._btn_stop.configure(state="disabled", fg_color="#ffffff",
                                  text_color="#bbbbbb", border_color="#dddddd")
        self._dot.configure(text_color="#bbbbbb")
        self._lbl_st.configure(text=" 待機中", text_color="#888888")
        self._render_msgs([])

    def _copy_url(self):
        self.clipboard_clear()
        self.clipboard_append(OBS_URL)
        self._btn_copy.configure(text="✓", text_color="#1a936f")
        self.after(1500, lambda: self._btn_copy.configure(
            text="コピー", text_color="#555555"))

    # API プリセット定義
    _API_PRESETS = [
        ("Cloudflare Tunnel（推奨）", "https://lt.f1234k.com/translate"),
        ("ローカル", "http://localhost:5000/translate"),
    ]

    def _open_api_settings(self, _=None):
        """翻訳API接続先の切り替えダイアログ（プリセット選択式）"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("翻訳API 設定")
        dlg.geometry("360x300")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)

        # 現在のURL取得
        current_url = translator.LIBRETRANSLATE_URL
        try:
            r = SESSION.get(f"http://localhost:{PORT}/lt_url", timeout=2)
            if r.ok:
                current_url = r.json().get("url", current_url)
        except: pass

        ctk.CTkLabel(dlg, text="翻訳サーバーの接続先",
                     font=ctk.CTkFont("Meiryo",13,"bold"),
                     fg_color="transparent").pack(padx=16, pady=(16,8), anchor="w")

        # ラジオボタン用変数
        choice = tk.StringVar(value="")
        # 現在のURLがプリセットに一致するか判定
        preset_match = False
        for name, url in self._API_PRESETS:
            if current_url == url:
                choice.set(url)
                preset_match = True
                break
        if not preset_match:
            choice.set("custom")

        # プリセット選択肢
        for name, url in self._API_PRESETS:
            rb = ctk.CTkRadioButton(dlg, text=name, variable=choice, value=url,
                                     font=ctk.CTkFont("Meiryo",12),
                                     fg_color="#29b6f6", hover_color="#4fc3f7")
            rb.pack(anchor="w", padx=20, pady=(2,2))

        # カスタム選択肢
        custom_rb = ctk.CTkRadioButton(dlg, text="カスタム", variable=choice,
                                        value="custom",
                                        font=ctk.CTkFont("Meiryo",12),
                                        fg_color="#29b6f6", hover_color="#4fc3f7")
        custom_rb.pack(anchor="w", padx=20, pady=(2,2))

        custom_entry = ctk.CTkEntry(dlg, font=ctk.CTkFont("Consolas",11),
                                     height=30, corner_radius=6, placeholder_text="https://...",
                                     fg_color="#ffffff", border_color="#dddddd",
                                     border_width=1, text_color="#333333")
        custom_entry.pack(fill="x", padx=40, pady=(2,8))
        if not preset_match:
            custom_entry.insert(0, current_url)

        status_lbl = ctk.CTkLabel(dlg, text="", font=ctk.CTkFont("Meiryo",10),
                                   fg_color="transparent")
        status_lbl.pack(padx=16, anchor="w")

        def _apply():
            val = choice.get()
            if val == "custom":
                new_url = custom_entry.get().strip()
                if not new_url:
                    status_lbl.configure(text="URLを入力してください", text_color="#c0392b")
                    return
            else:
                new_url = val
            try:
                r = SESSION.post(f"http://localhost:{PORT}/lt_url",
                                 json={"url": new_url}, timeout=3)
                if r.ok:
                    status_lbl.configure(text="✓ 切り替えました", text_color="#1a936f")
                    self.after(800, dlg.destroy)
                    self._check_lt()
                else:
                    status_lbl.configure(text="エラー", text_color="#c0392b")
            except Exception as e:
                status_lbl.configure(text=f"接続エラー", text_color="#c0392b")

        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(4,12))
        ctk.CTkButton(btn_frame, text="切り替え", fg_color="#29b6f6",
                      hover_color="#4fc3f7", text_color="#ffffff",
                      font=ctk.CTkFont("Meiryo",12,"bold"),
                      height=32, corner_radius=6, width=90,
                      command=_apply).pack(side="right", padx=(4,0))
        ctk.CTkButton(btn_frame, text="閉じる", fg_color="#ffffff",
                      hover_color="#f0efe8", text_color="#555555",
                      font=ctk.CTkFont("Meiryo",12),
                      border_width=1, border_color="#cccccc",
                      height=32, corner_radius=6, width=80,
                      command=dlg.destroy).pack(side="right")

    # ── メッセージ描画（永続スロット方式・ちらつきゼロ）──
    def _render_msgs(self, msgs):
        n = self._last_slot_count
        show = msgs[:n]
        if not show:
            for i in range(len(self._slots)):
                self._slots[i]["frame"].pack_forget()
                self._slot_keys[i] = None
            self._lbl_empty.pack(pady=30)
            return
        self._lbl_empty.pack_forget()

        for i in range(n):
            slot = self._slots[i]
            if i < len(show):
                m = show[i]
                key = f"{m.get('author','')}\\0{m.get('original','')}"
                if key == self._slot_keys[i]:
                    if not slot["frame"].winfo_ismapped():
                        slot["frame"].pack(fill="x", pady=(0,4))
                    continue
                self._slot_keys[i] = key
                translated = m.get("translated")
                original   = m.get("original","")
                author     = m.get("author","")
                lang       = m.get("lang","")
                lang_label = LANG.get(lang.lower(), lang)
                text       = translated if translated else original

                border_color = ACC if translated else "#555555"
                slot["border"].configure(bg=border_color)
                slot["author"].configure(text=author)
                img_url = m.get("imageUrl","")
                if img_url and img_url != slot["_av_url"]:
                    slot["_av_url"] = img_url
                    def _load(lbl=slot["avatar"], url=img_url):
                        img = fetch_avatar(url, size=22)
                        if img:
                            try: lbl.configure(image=img, text="")
                            except: pass
                    threading.Thread(target=_load, daemon=True).start()
                if translated:
                    slot["lang_badge"].configure(text=f" {lang_label} ")
                    slot["lang_badge"].pack(side="left", padx=(6,0))
                    slot["_lang_code"] = lang.lower()
                    slot["fb_btn"].pack(side="right", padx=(4,0))
                    slot["fb_btn"].bind("<Button-1>",
                        lambda e, s=slot: self._send_feedback(s))
                else:
                    slot["lang_badge"].pack_forget()
                    slot["fb_btn"].pack_forget()
                slot["text"].configure(text=text)
                if translated:
                    slot["original"].configure(text=original)
                    slot["original"].pack(fill="x", pady=(1,0))
                else:
                    slot["original"].pack_forget()
                if not slot["frame"].winfo_ismapped():
                    slot["frame"].pack(fill="x", pady=(0,4))
            else:
                slot["frame"].pack_forget()
                self._slot_keys[i] = None

    # ── ポーリング ──
    def _poll(self):
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/messages", timeout=1)
                if r.ok:
                    msgs = r.json()
                    translated_only = [m for m in msgs if m.get("translated")]
                    self.after(0, lambda: self._render_msgs(translated_only))
            except: pass
        threading.Thread(target=_do, daemon=True).start()
        self.after(800, self._poll)

    def _flash_refresh(self):
        """再接続成功時のフィードバック表示"""
        self._btn_start.configure(text="✓ 更新完了", state="normal",
                                   fg_color="#e8f5e9", text_color="#1a936f",
                                   border_color="#a0d8c0")
        self._dot.configure(text_color="#1a936f")
        def _restore():
            self._btn_start.configure(text="更新", fg_color="#f0faf5",
                                       hover_color="#e0f5eb",
                                       text_color="#1a936f", border_color="#a0d8c0")
        self.after(1500, _restore)

    def _send_feedback(self, slot):
        """翻訳品質フィードバックを送信"""
        lang_code = slot.get("_lang_code", "")
        stats.record_feedback(source_lang=lang_code)
        btn = slot["fb_btn"]
        btn.configure(text="✓", text_color="#1a936f")
        self.after(1500, lambda: btn.configure(text="👎", text_color="#666666"))

    def _check_lt(self):
        """翻訳API状態確認: 緑=OK / 黄=確認中 / 赤=エラー"""
        self.after(0, lambda: self._api_dot.configure(text_color="#f1c40f"))
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/lt_check", timeout=8)
                data = r.json() if r.ok else {}
                status = data.get("status", "")
                if status == "ok":
                    color = "#2ecc71"
                elif status == "warning":
                    color = "#f1c40f"
                else:
                    color = "#e74c3c"
                    err = data.get("error", "")
                    if err:
                        print(f"[疎通確認失敗][{data.get('engine','')}] {err}")
            except:
                color = "#e74c3c"
            self.after(0, lambda: self._api_dot.configure(text_color=color))
        threading.Thread(target=_do, daemon=True).start()
        self.after(30000, self._check_lt)

    def _on_close(self):
        self.destroy()
        os._exit(0)


if __name__ == "__main__":
    time.sleep(1.2)
    App().mainloop()
