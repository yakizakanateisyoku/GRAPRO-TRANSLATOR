"""
OBS Chat Translator - GUI (customtkinter版)
v1.0.0 - UI整理版
"""
import customtkinter as ctk
import tkinter as tk
import threading, time, sys, os, requests, webbrowser, io, random
from PIL import Image, ImageDraw
import json as _json

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import main as translator

_CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def _load_config():
    """config.json を読み込む"""
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            return _json.load(f)
    except:
        return {}

def _save_config(cfg):
    """config.json に保存"""
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, ensure_ascii=False, indent=2)
    except:
        pass

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
        self.title(f"GRAPRO-TRANSLATOR v{translator.VERSION}")
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
        self._poll_server_notification()
        self._check_update()

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

        # サーバー通知バー（警告・レート制限・ブロック）
        self._notif_bar = ctk.CTkFrame(card, fg_color="#2c2c2c", corner_radius=6, height=0)
        self._notif_lbl = ctk.CTkLabel(self._notif_bar, text="",
                                        font=ctk.CTkFont("Meiryo", 10),
                                        text_color="#ffffff", fg_color="transparent",
                                        wraplength=350, justify="left")
        self._notif_lbl.pack(padx=8, pady=4, fill="x")
        self._notif_dismiss = ctk.CTkLabel(self._notif_bar, text="✕",
                                            font=("Arial", 12), text_color="#aaaaaa",
                                            fg_color="transparent", cursor="hand2")
        self._notif_dismiss.place(relx=1.0, rely=0.0, anchor="ne", x=-6, y=4)
        self._notif_dismiss.bind("<Button-1>", lambda e: self._hide_notification())
        # 初期は非表示
        self._notif_visible = False

        # 入力ラベル（プラットフォーム名クリックでURL自動入力）
        self._url_label_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._url_label_frame.pack(anchor="w", padx=14, pady=(10,3))
        url_label_f = self._url_label_frame
        ctk.CTkLabel(url_label_f, text="配信URL（",
                     text_color="#888888", font=ctk.CTkFont("Meiryo",11),
                     fg_color="transparent").pack(side="left")
        self._platform_lbls = []
        for pname, pkey in [("YouTube","youtube"), ("Twitch","twitch"), ("ツイキャス","twitcasting")]:
            lbl = ctk.CTkLabel(url_label_f, text=pname,
                               text_color=ACC, font=ctk.CTkFont("Meiryo",11,"bold"),
                               fg_color="transparent", cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, k=pkey: self._fill_channel(k))
            self._platform_lbls.append(lbl)
            sep = " / " if pkey != "twitcasting" else ""
            if sep:
                ctk.CTkLabel(url_label_f, text=sep,
                             text_color="#888888", font=ctk.CTkFont("Meiryo",11),
                             fg_color="transparent").pack(side="left")
        ctk.CTkLabel(url_label_f, text="）",
                     text_color="#888888", font=ctk.CTkFont("Meiryo",11),
                     fg_color="transparent").pack(side="left")

        # 入力欄
        self._entry = ctk.CTkEntry(
            card, placeholder_text="https://youtube.com/watch?v=... or twitch.tv/...",
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
        self._filter_translated = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(card, text="翻訳済みコメントのみ",
                      variable=self._filter_translated,
                      text_color="#888888", font=ctk.CTkFont("Meiryo",11),
                      fg_color="#dddddd", progress_color="#29b6f6",
                      button_color="#ffffff", button_hover_color="#f0f0f0",
                      width=40, height=20
                      ).pack(anchor="w", padx=14, pady=(0,4))

        self._msg_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._msg_frame.pack(fill="both", expand=True, padx=14, pady=(0,12))

        self._lbl_empty = ctk.CTkLabel(
            self._msg_frame, text="開始するとここに表示されます",
            text_color="#bbbbbb", font=ctk.CTkFont("Meiryo",10),
            fg_color="transparent")
        self._lbl_empty.pack(pady=30)

        # スロット水続生成（ダークカード）・初期5個、リサイズで増減
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
        """ウィンドウ縮リサイズ時にスロット数を調整"""
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
        """URL/IDの検証。YouTube, Twitch, ツイキャス, DEMO に対応"""
        raw = raw.strip()
        if not raw: return None
        # DEMO
        if raw.upper().startswith("DEMO"):
            return raw
        # Twitch URL: そのまま渡す（main.py側で判定）
        if "twitch.tv/" in raw:
            return raw
        # ツイキャス URL: そのまま渡す
        if "twitcasting.tv/" in raw:
            return raw
        # YouTube URL
        if "youtube.com" in raw or "youtu.be" in raw:
            if "v=" in raw:
                raw = raw.split("v=")[-1].split("&")[0]
            elif "youtu.be/" in raw:
                raw = raw.split("youtu.be/")[-1].split("?")[0]
        if len(raw) >= 4:
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
                    if vid.upper().startswith("DEMO"):
                        self.after(500, self._run_demo)
            except:
                self.after(0, lambda: self._lbl_st.configure(
                    text=f" 接続エラー", text_color="#c0392b"))
        # 配信中なら「更新」として再接続
        was_streaming = self._streaming
        if was_streaming:
            pass  # TODO: 未翻訳を再翻訳（_retranslate）
        if was_streaming:
            self._btn_start.configure(text="再接続中…", state="disabled",
                                       text_color="#999999")
            self._dot.configure(text_color="#f1c40f")
            self._lbl_st.configure(text=" 再接続中…", text_color="#f1c40f")
        self._streaming = True
        self._entry.configure(state="disabled", fg_color="#f5f5f5", text_color="#999999")
        for lbl in self._platform_lbls:
            lbl.configure(text_color="#bbbbbb", cursor="arrow")
        self._btn_start.configure(text="更新", fg_color="#f0faf5",
                                   hover_color="#e0f5eb",
                                   text_color="#1a936f", border_color="#a0d8c0")
        self._btn_stop.configure(state="normal", fg_color="#fff0f0",
                                  hover_color="#ffe0e0", text_color="#c0392b",
                                  border_color="#f5a0a0")
        self._dot.configure(text_color="#1a936f")
        self._lbl_st.configure(text=f" 開始中…", text_color="#1a936f")
        threading.Thread(target=_do, daemon=True).start()

    def _fill_channel(self, platform_key):
        """マイチャンネルから保存済みURLを入力欄に自動入力"""
        cfg = _load_config()
        channels = cfg.get("my_channels", {})
        url = channels.get(platform_key, "")
        if url:
            if self._streaming:
                return  # 配信中は変更不可
            self._entry.delete(0, "end")
            self._entry.insert(0, url)
        else:
            # 未登録 → ヒント表示
            self._lbl_st.configure(
                text=f" ⚙ マイチャンネルで{platform_key}を登録してね",
                text_color="#e67e22")

    def _stop(self):
        try: SESSION.get(f"http://localhost:{PORT}/stop", timeout=3)
        except: pass
        self._streaming = False
        self._entry.configure(state="normal", fg_color="#ffffff", text_color="#333333")
        for lbl in self._platform_lbls:
            lbl.configure(text_color=ACC, cursor="hand2")
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
        ("GRAPROサーバー（推奨）", "https://lt.f1234k.com/translate"),
        ("自分のPC", "http://localhost:5000/translate"),
    ]

    def _open_api_settings(self, _=None):
        """設定ダイアログ（翻訳 / マイチャンネル タブ）"""
        dlg = ctk.CTkToplevel(self)
        dlg.title("設定")
        dlg.geometry("400x540")
        dlg.resizable(False, False)
        dlg.grab_set()
        dlg.transient(self)

        # --- タブボタン ---
        tab_bar = ctk.CTkFrame(dlg, fg_color="transparent")
        tab_bar.pack(fill="x", padx=16, pady=(12,0))
        tab_frames = {}
        tab_btns = {}

        def _switch_tab(name):
            for k, f in tab_frames.items():
                f.pack_forget()
                tab_btns[k].configure(fg_color="#e0e0e0", text_color="#555555")
            tab_frames[name].pack(fill="both", expand=True, padx=16, pady=(8,0))
            tab_btns[name].configure(fg_color=ACC, text_color="#ffffff")

        for tab_name in ["翻訳", "マイチャンネル"]:
            btn = ctk.CTkButton(tab_bar, text=tab_name, fg_color="#e0e0e0",
                                hover_color="#d0d0d0", text_color="#555555",
                                font=ctk.CTkFont("Meiryo",11,"bold"),
                                height=28, corner_radius=6, width=100,
                                command=lambda n=tab_name: _switch_tab(n))
            btn.pack(side="left", padx=(0,4))
            tab_btns[tab_name] = btn
            f = ctk.CTkFrame(dlg, fg_color="transparent")
            tab_frames[tab_name] = f

        # ===== 翻訳タブ =====
        tf = tab_frames["翻訳"]

        # 現在の設定を取得
        current_url = translator.LIBRETRANSLATE_URL
        current_engine = "libretranslate"
        try:
            r = SESSION.get(f"http://localhost:{PORT}/lt_url", timeout=2)
            if r.ok:
                d = r.json()
                current_url = d.get("url", current_url)
                current_engine = d.get("engine", current_engine)
        except: pass

        # --- 翻訳エンジン選択 ---
        ctk.CTkLabel(tf, text="翻訳エンジン",
                     font=ctk.CTkFont("Meiryo",12,"bold"),
                     fg_color="transparent").pack(pady=(8,4), anchor="w")

        engine_var = tk.StringVar(value=current_engine)
        lt_frame = ctk.CTkFrame(tf, fg_color="transparent")  # LibreTranslate詳細（後でpack）

        def _on_engine_change():
            if engine_var.get() == "libretranslate":
                lt_frame.pack(fill="x", after=engine_area, pady=(4,0))
            else:
                lt_frame.pack_forget()

        engine_area = ctk.CTkFrame(tf, fg_color="transparent")
        engine_area.pack(fill="x")
        ctk.CTkRadioButton(engine_area, text="GRAPROサーバー（推奨）",
                           variable=engine_var, value="grapro",
                           font=ctk.CTkFont("Meiryo",11),
                           fg_color=ACC, hover_color="#4fc3f7",
                           command=_on_engine_change).pack(anchor="w", padx=4, pady=(2,0))
        ctk.CTkLabel(engine_area, text="高精度・言語自動検出。Azure中継サーバー経由",
                     text_color="#999999", font=ctk.CTkFont("Meiryo",9),
                     fg_color="transparent").pack(anchor="w", padx=28, pady=(0,2))
        ctk.CTkRadioButton(engine_area, text="DeepL（非常用）",
                           variable=engine_var, value="deepl",
                           font=ctk.CTkFont("Meiryo",11),
                           fg_color=ACC, hover_color="#4fc3f7",
                           command=_on_engine_change).pack(anchor="w", padx=4, pady=(2,0))
        ctk.CTkLabel(engine_area, text="高精度。月50万文字まで無料",
                     text_color="#999999", font=ctk.CTkFont("Meiryo",9),
                     fg_color="transparent").pack(anchor="w", padx=28, pady=(0,2))
        ctk.CTkRadioButton(engine_area, text="LibreTranslate",
                           variable=engine_var, value="libretranslate",
                           font=ctk.CTkFont("Meiryo",11),
                           fg_color=ACC, hover_color="#4fc3f7",
                           command=_on_engine_change).pack(anchor="w", padx=4, pady=(2,0))
        ctk.CTkLabel(engine_area, text="無料・軽量。精度は控えめ",
                     text_color="#999999", font=ctk.CTkFont("Meiryo",9),
                     fg_color="transparent").pack(anchor="w", padx=28, pady=(0,2))

        # --- LibreTranslate サーバー選択 ---
        ctk.CTkLabel(lt_frame, text="サーバー",
                     font=ctk.CTkFont("Meiryo",11,"bold"),
                     fg_color="transparent").pack(pady=(4,2), anchor="w")

        choice = tk.StringVar(value="")
        preset_match = False
        for name, url in self._API_PRESETS:
            if current_url == url:
                choice.set(url)
                preset_match = True
                break
        if not preset_match:
            choice.set("custom")

        _preset_desc = {
            "GRAPROサーバー（推奨）": "安定・高速。そのまま使えます",
            "自分のPC": "LibreTranslateを自分で動かす場合",
        }
        for name, url in self._API_PRESETS:
            rb = ctk.CTkRadioButton(lt_frame, text=name, variable=choice, value=url,
                                     font=ctk.CTkFont("Meiryo",10),
                                     fg_color=ACC, hover_color="#4fc3f7")
            rb.pack(anchor="w", padx=4, pady=(1,0))
            desc = _preset_desc.get(name, "")
            if desc:
                ctk.CTkLabel(lt_frame, text=desc, text_color="#999999",
                             font=ctk.CTkFont("Meiryo",8),
                             fg_color="transparent").pack(anchor="w", padx=28, pady=(0,1))

        custom_rb = ctk.CTkRadioButton(lt_frame, text="その他のサーバー", variable=choice,
                                        value="custom", font=ctk.CTkFont("Meiryo",10),
                                        fg_color=ACC, hover_color="#4fc3f7")
        custom_rb.pack(anchor="w", padx=4, pady=(1,0))

        custom_entry = ctk.CTkEntry(lt_frame, font=ctk.CTkFont("Consolas",10),
                                     height=28, corner_radius=6, placeholder_text="https://...",
                                     fg_color="#ffffff", border_color="#dddddd",
                                     border_width=1, text_color="#333333")
        custom_entry.pack(fill="x", padx=24, pady=(2,4))
        if not preset_match:
            custom_entry.insert(0, current_url)

        # 初期表示
        if current_engine == "libretranslate":
            lt_frame.pack(fill="x", after=engine_area, pady=(4,0))

        # --- ステータスと適用ボタン ---
        api_status = ctk.CTkLabel(tf, text="", font=ctk.CTkFont("Meiryo",10),
                                   fg_color="transparent")
        api_status.pack(anchor="w")

        def _apply_api():
            eng = engine_var.get()
            payload = {"engine": eng}
            if eng == "libretranslate":
                val = choice.get()
                new_url = custom_entry.get().strip() if val == "custom" else val
                if not new_url:
                    api_status.configure(text="URLを入力してください", text_color="#c0392b")
                    return
                payload["url"] = new_url
            try:
                r = SESSION.post(f"http://localhost:{PORT}/lt_url",
                                 json=payload, timeout=3)
                if r.ok:
                    label = {"grapro": "GRAPROサーバー", "libretranslate": "LibreTranslate", "deepl": "DeepL"}.get(eng, eng)
                    api_status.configure(text=f"✓ {label}に切り替えました", text_color="#1a936f")
                    self._check_lt()
                else:
                    api_status.configure(text="エラー", text_color="#c0392b")
            except:
                api_status.configure(text="接続エラー", text_color="#c0392b")

        ctk.CTkButton(tf, text="変更する", fg_color=ACC, hover_color="#4fc3f7",
                      text_color="#ffffff", font=ctk.CTkFont("Meiryo",11,"bold"),
                      height=30, corner_radius=6, width=90,
                      command=_apply_api).pack(anchor="e", pady=(4,0))

        # ===== マイチャンネルタブ =====
        cf = tab_frames["マイチャンネル"]
        ctk.CTkLabel(cf, text="よく使う配信チャンネルを登録",
                     font=ctk.CTkFont("Meiryo",12,"bold"),
                     fg_color="transparent").pack(pady=(8,6), anchor="w")
        ctk.CTkLabel(cf, text="プラットフォーム名をクリックするとURLが自動入力されます",
                     font=ctk.CTkFont("Meiryo",9), text_color="#999999",
                     fg_color="transparent").pack(anchor="w", pady=(0,8))

        cfg = _load_config()
        channels = cfg.get("my_channels", {})
        ch_entries = {}
        for pname, pkey, placeholder in [
            ("YouTube", "youtube", "https://youtube.com/watch?v=... or チャンネルURL"),
            ("Twitch", "twitch", "https://twitch.tv/ユーザー名"),
            ("ツイキャス", "twitcasting", "https://twitcasting.tv/ユーザー名"),
        ]:
            ctk.CTkLabel(cf, text=pname, font=ctk.CTkFont("Meiryo",11,"bold"),
                         text_color="#333333",
                         fg_color="transparent").pack(anchor="w", pady=(4,1))
            ent = ctk.CTkEntry(cf, font=ctk.CTkFont("Consolas",10),
                               height=28, corner_radius=6,
                               placeholder_text=placeholder,
                               fg_color="#ffffff", border_color="#dddddd",
                               border_width=1, text_color="#333333")
            ent.pack(fill="x", pady=(0,4))
            saved = channels.get(pkey, "")
            if saved:
                ent.insert(0, saved)
            ch_entries[pkey] = ent

        ch_status = ctk.CTkLabel(cf, text="", font=ctk.CTkFont("Meiryo",10),
                                  fg_color="transparent")
        ch_status.pack(anchor="w", pady=(4,0))

        def _save_channels():
            cfg = _load_config()
            cfg["my_channels"] = {
                k: ent.get().strip() for k, ent in ch_entries.items()
            }
            _save_config(cfg)
            ch_status.configure(text="✓ 保存しました", text_color="#1a936f")

        ctk.CTkButton(cf, text="保存", fg_color=ACC, hover_color="#4fc3f7",
                      text_color="#ffffff", font=ctk.CTkFont("Meiryo",11,"bold"),
                      height=30, corner_radius=6, width=90,
                      command=_save_channels).pack(anchor="e", pady=(4,0))

        # --- 共通: 閉じるボタン（最下部に固定）---
        bottom_bar = ctk.CTkFrame(dlg, fg_color="transparent")
        bottom_bar.pack(side="bottom", fill="x", padx=16, pady=(8,12))
        ctk.CTkButton(bottom_bar, text="閉じる", fg_color="#ffffff",
                      hover_color="#f0efe8", text_color="#555555",
                      font=ctk.CTkFont("Meiryo",11),
                      border_width=1, border_color="#cccccc",
                      height=30, corner_radius=6, width=80,
                      command=dlg.destroy).pack(anchor="center")

        _switch_tab("翻訳")

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
                    translated_only = [m for m in msgs if m.get("translated")] if self._filter_translated.get() else msgs
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
        # stats.record_feedback(source_lang=lang_code)  # TODO: stats module未実装
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

    def _show_notification(self, msg, ntype="warn"):
        """通知バーを表示。ntype: warn=黄, rate_limit=橙, blocked=赤"""
        colors = {
            "warn":       ("#5c4a00", "#ffd54f"),   # 背景, 文字
            "rate_limit": ("#4a2800", "#ffab40"),
            "blocked":    ("#4a0000", "#ff5252"),
        }
        bg, fg = colors.get(ntype, colors["warn"])
        icons = {"warn": "⚠", "rate_limit": "⏱", "blocked": "🚫"}
        icon = icons.get(ntype, "⚠")
        self._notif_bar.configure(fg_color=bg)
        self._notif_lbl.configure(text=f"{icon}  {msg}", text_color=fg)
        if not self._notif_visible:
            self._notif_bar.pack(fill="x", padx=14, pady=(4, 0), before=self._url_label_frame)
            self._notif_visible = True

    def _hide_notification(self):
        """通知バーを非表示"""
        if self._notif_visible:
            self._notif_bar.pack_forget()
            self._notif_visible = False

    def _poll_server_notification(self):
        """サーバー通知をポーリング（10秒間隔）"""
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/server_notification", timeout=5)
                data = r.json() if r.ok else {}
                ntype = data.get("type")
                if ntype:
                    msg = data.get("message", "サーバーからの通知")
                    self.after(0, lambda: self._show_notification(msg, ntype))
            except:
                pass
        threading.Thread(target=_do, daemon=True).start()
        self.after(10000, self._poll_server_notification)

    # ── デモモード（30件コメント自動注入）──
    _DEMO_JA = [
        {"author": "たかふみ", "original": "おつかれ〜", "translated": None, "lang": "ja"},
        {"author": "ゲーム好き太郎", "original": "きたきた！待ってました", "translated": None, "lang": "ja"},
        {"author": "さくら", "original": "こんばんは〜初見です！", "translated": None, "lang": "ja"},
        {"author": "まさき_ch", "original": "ナイスー！", "translated": None, "lang": "ja"},
        {"author": "夜更かし勢", "original": "今日も配信ありがとう", "translated": None, "lang": "ja"},
        {"author": "りょうた", "original": "うまいなぁ", "translated": None, "lang": "ja"},
        {"author": "みかん", "original": "www", "translated": None, "lang": "ja"},
        {"author": "ゆっくり勢", "original": "この後どうするの？", "translated": None, "lang": "ja"},
        {"author": "たけし", "original": "もう一回やって！", "translated": None, "lang": "ja"},
        {"author": "ねこまる", "original": "8888888", "translated": None, "lang": "ja"},
        {"author": "あいり", "original": "かわいい〜", "translated": None, "lang": "ja"},
        {"author": "しゅん_fps", "original": "立ち回りうますぎ", "translated": None, "lang": "ja"},
        {"author": "まなみ", "original": "今何時間目？", "translated": None, "lang": "ja"},
        {"author": "だいき", "original": "エイム神", "translated": None, "lang": "ja"},
        {"author": "ほのか", "original": "BGMいい感じ", "translated": None, "lang": "ja"},
        {"author": "ゲーマーけん", "original": "次のマッチ楽しみ", "translated": None, "lang": "ja"},
        {"author": "ともや", "original": "おー！すごい！", "translated": None, "lang": "ja"},
        {"author": "はるき", "original": "初見だけどハマりそう", "translated": None, "lang": "ja"},
        {"author": "ゆうな", "original": "がんばれぜ！", "translated": None, "lang": "ja"},
        {"author": "れん", "original": "ここ難しいよね", "translated": None, "lang": "ja"},
    ]
    _DEMO_FOREIGN = [
        {"author": "Jake_TTV", "original": "yo this stream is fire", "translated": "この配信めっちゃアツい", "lang": "en"},
        {"author": "GG_Chris", "original": "nice play dude, that was insane", "translated": "ナイスプレイ、やばかった", "lang": "en"},
        {"author": "Carlos_MX", "original": "saludos desde Mexico!", "translated": "メキシコから挨拶！", "lang": "es"},
        {"author": "LuciaSP", "original": "me encanta tu stream", "translated": "あなたの配信大好き", "lang": "es"},
        {"author": "XiaoMing", "original": "第一次看你的直播，很有趣！", "translated": "初めて配信見ました、面白い！", "lang": "zh-cn"},
        {"author": "DaWei888", "original": "哈哈哈太搞笑了", "translated": "www 面白すぎるwww", "lang": "zh-cn"},
        {"author": "SoYeon_KR", "original": "bangsong neomu jaemisseoyo", "translated": "配信めっちゃ面白いですwww", "lang": "ko"},
        {"author": "Pierre_FR", "original": "salut depuis la France!", "translated": "フランスからこんにちは！", "lang": "fr"},
        {"author": "Ivan_RU", "original": "Privet iz Rossii!", "translated": "ロシアからこんにちは！", "lang": "ru"},
        {"author": "LucasBR", "original": "boa noite do Brasil!", "translated": "ブラジルからこんばんは！", "lang": "pt"},
        {"author": "Tommy_US", "original": "lol that was so funny", "translated": "www めっちゃ面白い", "lang": "en"},
        {"author": "MariaES", "original": "que bonito juego!", "translated": "きれいなゲームだね！", "lang": "es"},
        {"author": "KimJH_KR", "original": "daebak! jal handa", "translated": "すごい！マジでうまい", "lang": "ko"},
        {"author": "AnneFR", "original": "bravo, c'est magnifique !", "translated": "ブラボー、素晴らしい！", "lang": "fr"},
        {"author": "WeiLin_TW", "original": "加油加油！", "translated": "がんばれがんばれ！", "lang": "zh-tw"},
        {"author": "Alex_UK", "original": "this game looks amazing", "translated": "このゲームすごそう", "lang": "en"},
        {"author": "HanaBR", "original": "stream muito bom!", "translated": "配信めっちゃいい！", "lang": "pt"},
        {"author": "MinJi_KR", "original": "eungwonhae!", "translated": "応援してるよ！", "lang": "ko"},
        {"author": "Marco_IT", "original": "grande stream!", "translated": "最高の配信！", "lang": "it"},
        {"author": "Sven_DE", "original": "super gameplay!", "translated": "すごいプレイ！", "lang": "de"},
    ]

    def _run_demo(self):
        """DEMOモード: 停止まで日本語・外国語交互に1秒1コメント流す"""
        ja_pool = list(self._DEMO_JA)
        fg_pool = list(self._DEMO_FOREIGN)
        random.shuffle(ja_pool)
        random.shuffle(fg_pool)
        self._demo_ja_idx = 0
        self._demo_fg_idx = 0
        self._demo_is_ja = True

        def _inject(visible):
            if not self._streaming:
                return
            if self._demo_is_ja:
                msg = ja_pool[self._demo_ja_idx % len(ja_pool)]
                self._demo_ja_idx += 1
            else:
                msg = fg_pool[self._demo_fg_idx % len(fg_pool)]
                self._demo_fg_idx += 1
            self._demo_is_ja = not self._demo_is_ja
            visible.insert(0, msg)
            batch = visible[:5]
            try:
                SESSION.post(f"http://localhost:{PORT}/test_batch", json=batch, timeout=2)
            except:
                pass
            self.after(1000, lambda: _inject(visible))

        _inject([])

    def _check_update(self):
        """起動後にバックグラウンドでアップデート確認"""
        def _do():
            try:
                r = SESSION.get(f"http://localhost:{PORT}/update_check", timeout=5)
                if r.ok:
                    data = r.json()
                    if data.get("update_available"):
                        latest = data.get("latest", "")
                        url = data.get("url", "")
                        self.after(0, lambda: self._show_update(latest, url))
            except:
                pass
        self.after(3000, lambda: threading.Thread(target=_do, daemon=True).start())

    def _show_update(self, latest, url):
        """アップデート通知バーを表示"""
        uf = ctk.CTkFrame(self._card, fg_color="#fff8e1", corner_radius=6, height=28)
        uf.pack(fill="x", padx=14, pady=(0, 4))
        lbl = ctk.CTkLabel(uf, text=f"v{latest} が利用可能",
                           text_color="#f57c00", font=ctk.CTkFont("Meiryo", 10),
                           fg_color="transparent")
        lbl.pack(side="left", padx=(8, 0))
        btn = ctk.CTkLabel(uf, text="ダウンロード", text_color="#1976d2",
                           font=ctk.CTkFont("Meiryo", 10, "bold"),
                           fg_color="transparent", cursor="hand2")
        btn.pack(side="left", padx=(6, 0))
        btn.bind("<Button-1>", lambda e: webbrowser.open(url))

    def _on_close(self):
        # 全ワーカーに停止シグナルを送る
        translator.stop_event.set()
        self.destroy()
        os._exit(0)


if __name__ == "__main__":
    time.sleep(1.2)
    App().mainloop()
