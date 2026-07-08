"""
GRAPRO-TRANSLATOR - ライブ配信チャット翻訳ツール
- YouTube InnerTube API / Twitch IRC / ツイキャス API v2 でチャットを直接取得
- 言語検出・翻訳は GRAPRO 中継サーバー（Azure Translator）側に委任
- キュー処理・複数ワーカーで並列翻訳
- Flask で OBS ブラウザソース用オーバーレイを提供

使い方:
  python main.py                  # サーバーのみ起動
  python main.py <video_id>       # 起動時にチャット開始
  GET /start/<video_id>           # チャット開始（要トークン: ?token=XXX）
  GET /stop                       # チャット停止（要トークン）
  GET /status                     # 状態確認
  GET /test                       # ダミーデータ注入（UIテスト用）
  GET /lt_check                   # 翻訳エンジン疎通確認（実翻訳・要トークン）
  GET /api_health                 # 軽量ヘルスチェック（翻訳なし・課金なし）
  ※ トークンは起動時にコンソールへ表示されます（GUI利用時は自動付与）
"""

VERSION = "1.5.0"  # 2026/07/08: LibreTranslate復旧・worker_id永続化・APIトークン保護・翻訳キャッシュ・再接続強化

import threading
import time
import queue
import sys
import os
import uuid as _uuid
import itertools as _itertools
import socket as _socket
import re as _re
import json as _json
from collections import OrderedDict
from flask import Flask, render_template_string, jsonify, request as flask_request
import requests
# langdetect は廃止済み — 言語検出は翻訳API（Azure/GRAPRO）側に委任

# ===== パス解決 =====
def _base_dir():
    """exe（またはスクリプト）隣接の永続ディレクトリを返す。
    PyInstaller onefile では __file__ が一時展開フォルダ(_MEIPASS)を指し
    終了時に消えるため、必ず sys.executable 基準にする。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = _base_dir()

# ===== 設定 =====
OVERLAY_PORT       = 7788
MAX_MESSAGES       = 20    # オーバーレイ表示の最大件数
MIN_CHARS          = 3     # 翻訳する最小文字数
TARGET_LANG        = "ja"  # 翻訳先言語
NUM_WORKERS        = 3     # 翻訳ワーカースレッド数

# ===== 翻訳エンジン設定 =====
# "grapro" (中継サーバー経由) | "libretranslate" | "deepl"
TRANSLATE_ENGINE = os.environ.get("TRANSLATE_ENGINE", "grapro")

# --- GRAPRO 中継サーバー（推奨）---
# APIキーはサーバー側にのみ保持。クライアントには配布しない。
GRAPRO_TRANSLATE_URL = "https://lt.f1234k.com/relay/translate"

# --- LibreTranslate（フォールバック）---
LIBRETRANSLATE_URL     = "https://lt.f1234k.com/translate"
LIBRETRANSLATE_API_KEY = ""  # サーバー側で管理

# --- DeepL（非常用・フォールバック）---
# ユーザーが自分のAPIキーを設定する場合のみ使用
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "")
DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"

# 言語コード → 日本語表示名
LANG_NAMES = {
    "af": "アフリカーンス語", "ar": "アラビア語", "az": "アゼルバイジャン語",
    "bg": "ブルガリア語", "bn": "ベンガル語", "ca": "カタルーニャ語",
    "cs": "チェコ語", "cy": "ウェールズ語", "da": "デンマーク語",
    "de": "ドイツ語", "el": "ギリシャ語", "en": "英語",
    "eo": "エスペラント語", "es": "スペイン語", "et": "エストニア語",
    "eu": "バスク語", "fa": "ペルシャ語", "fi": "フィンランド語",
    "fr": "フランス語", "ga": "アイルランド語", "gl": "ガリシア語",
    "gu": "グジャラート語", "he": "ヘブライ語", "hi": "ヒンディー語",
    "hr": "クロアチア語", "hu": "ハンガリー語", "hy": "アルメニア語",
    "id": "インドネシア語", "is": "アイスランド語", "it": "イタリア語",
    "ja": "日本語", "ka": "ジョージア語", "kk": "カザフ語",
    "km": "クメール語", "ko": "韓国語", "ky": "キルギス語",
    "lt": "リトアニア語", "lv": "ラトビア語", "mk": "マケドニア語",
    "ml": "マラヤーラム語", "mn": "モンゴル語", "mr": "マラーティー語",
    "ms": "マレー語", "mt": "マルタ語", "nb": "ノルウェー語",
    "nl": "オランダ語", "pl": "ポーランド語", "pt": "ポルトガル語",
    "pt-br": "ポルトガル語(BR)", "ro": "ルーマニア語", "ru": "ロシア語",
    "sk": "スロバキア語", "sl": "スロベニア語", "sq": "アルバニア語",
    "sr": "セルビア語", "sv": "スウェーデン語", "sw": "スワヒリ語",
    "ta": "タミル語", "te": "テルグ語", "th": "タイ語",
    "tl": "タガログ語", "tr": "トルコ語", "uk": "ウクライナ語",
    "ur": "ウルドゥー語", "vi": "ベトナム語",
    "zh": "中国語", "zh-cn": "中国語", "zh-hans": "中国語(簡体)",
    "zh-tw": "中国語(繁体)", "zh-hant": "中国語(繁体)",
}

# ===== グローバル状態 =====
chat_messages   = []          # 表示用メッセージリスト
messages_lock   = threading.Lock()
TRANSLATION_QUEUE_MAX = 300   # 翻訳キュー上限（無制限だと翻訳API停止時にメモリ肥大）
translation_q   = queue.Queue(maxsize=TRANSLATION_QUEUE_MAX)   # 翻訳キュー
stop_event      = threading.Event()
chat_thread     = None
worker_threads  = []
_last_video_id  = None        # watchdog 再起動用に直近の配信IDを保持
_MSG_COUNTER    = _itertools.count(1)   # メッセージ一意ID（同一コメント連投の表示潰れ防止）

_q_drop_count = 0

def _enqueue_translation(item):
    """翻訳キューへ投入。満杯時は最古を捨てて新着を優先（翻訳API停止時の詰まり対策）"""
    global _q_drop_count
    try:
        translation_q.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        translation_q.get_nowait()
        translation_q.task_done()
    except queue.Empty:
        pass
    try:
        translation_q.put_nowait(item)
    except queue.Full:
        pass
    _q_drop_count += 1
    if _q_drop_count % 50 == 1:
        print(f"[キュー] 翻訳キュー満杯のため古いコメントを破棄 (累計{_q_drop_count})")


def _reconnect_wait(retry):
    """再接続前の待機。指数バックオフ（3秒→最大60秒）。stop_event で即中断"""
    wait = min(60, 3 * (2 ** min(retry, 5)))
    stop_event.wait(wait)

# ----- SHOWROOM 盛り上がり度数 -----
SHOWROOM_API_BASE = "https://www.showroom-live.com/api"
SHOWROOM_POLL_INTERVAL = 30
_showroom_data  = {"online_user_num": 0, "room_id": None, "active": False}
_showroom_lock  = threading.Lock()
_showroom_stop  = threading.Event()
_showroom_thread = None

def _showroom_poller(room_id):
    print(f"[SHOWROOM] polling start room_id={room_id}")
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    while not _showroom_stop.is_set():
        try:
            r = s.get(f"{SHOWROOM_API_BASE}/live/polling", params={"room_id": room_id}, timeout=10)
            if r.status_code == 200:
                with _showroom_lock:
                    _showroom_data["online_user_num"] = r.json().get("online_user_num", 0)
                    _showroom_data["active"] = True
        except Exception as e:
            print(f"[SHOWROOM] error: {e}")
        w = 0
        while w < SHOWROOM_POLL_INTERVAL and not _showroom_stop.is_set():
            time.sleep(1); w += 1
    with _showroom_lock:
        _showroom_data["active"] = False
    print("[SHOWROOM] polling stop")

def start_showroom(room_id):
    global _showroom_thread
    _showroom_stop.clear()
    with _showroom_lock:
        _showroom_data.update({"room_id": room_id, "online_user_num": 0, "active": False})
    if _showroom_thread and _showroom_thread.is_alive():
        return False
    _showroom_thread = threading.Thread(target=_showroom_poller, args=(room_id,), daemon=True)
    _showroom_thread.start()
    return True

def stop_showroom():
    _showroom_stop.set()
    with _showroom_lock:
        _showroom_data.update({"online_user_num": 0, "active": False, "room_id": None})

# ===== 棒読みちゃん連携 =====
_bouyomi_enabled = False
_bouyomi_port    = 50001

def _send_bouyomi(text: str):
    """棒読みちゃんにTCPソケットで読み上げ送信（失敗時はサイレント無視）"""
    if not _bouyomi_enabled or not text:
        return
    try:
        import socket as _sock, struct as _struct
        msg = text.encode("utf-8")
        # コマンド=0x0001(読み上げ), 速度=-1, 音程=-1, 音量=-1, 声質=0, 文字コード=0(UTF-8), 長さ
        header = _struct.pack("<hhhhhbI", 0x0001, -1, -1, -1, 0, 0, len(msg))
        with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(("127.0.0.1", _bouyomi_port))
            s.sendall(header + msg)
    except Exception:
        pass  # 棒読みちゃん未起動時など — サイレント無視

app = Flask(__name__)

# ===== ローカルAPI保護トークン =====
# Flask は 127.0.0.1 バインドだが、ブラウザで開いた悪意あるページからの
# 単純POST（CSRF）や GET リンク踏みで /stop /lt_url 等を叩ける穴があるため、
# 状態変更系エンドポイントは起動ごとに生成するトークンを必須にする。
# GUI・オーバーレイは自動で付与。手動でブラウザから叩く場合は ?token=XXX を付ける。
API_TOKEN = _uuid.uuid4().hex

# トークン必須の GET エンドポイント（POST は一律必須）
# ※ /test /showroom/* は手動ブラウザ操作の利便性を優先して除外（実害が軽微なため）
_PROTECTED_GET_PREFIXES = ("/start/", "/stop", "/lt_check")

@app.before_request
def _token_guard():
    p = flask_request.path
    needs = (flask_request.method == "POST"
             or any(p == pre.rstrip("/") or p.startswith(pre)
                    for pre in _PROTECTED_GET_PREFIXES))
    if not needs:
        return None
    tok = flask_request.headers.get("X-Grapro-Token") or flask_request.args.get("token")
    if tok != API_TOKEN:
        return jsonify({"error": "forbidden",
                        "message": "APIトークンが必要です（?token=XXX または X-Grapro-Token ヘッダー）"}), 403
    return None

# ===== オーバーレイ設定（サーバー側永続化） =====
# CWD相対だと起動方法（OBS自動起動・管理者実行等）で保存場所が変わるため exe 隣接に固定
_SETTINGS_FILE = os.path.join(BASE_DIR, "overlay_settings.json")
_DEFAULT_SETTINGS = {
    "bodyBg":        "transparent",
    "msgBg":         "rgba(0,0,0,0.88)",
    "accentTranslated": "#29b6f6",
    "accentJapanese":   "#555555",
    "authorColor":   "#ffe066",
    "authorFont":    "Meiryo, Noto Sans JP, sans-serif",
    "authorSize":    "14",
    "textColor":     "#ffffff",
    "textFont":      "Meiryo, Noto Sans JP, sans-serif",
    "textSize":      "18",
    "originalColor": "#bbbbbb",
    "count":         "5",
    "showroomHype":  "off",
}
_overlay_settings = dict(_DEFAULT_SETTINGS)

def _load_settings():
    global _overlay_settings
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = _json.load(f)
            _overlay_settings = {**_DEFAULT_SETTINGS, **saved}
    except Exception as e:
        print(f"[設定読込エラー] {e}")

def _save_settings():
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            _json.dump(_overlay_settings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[設定保存エラー] {e}")

_load_settings()


# ===== 翻訳・言語判定 =====

# 翻訳API用の共有セッション（接続再利用 + 一時エラー自動リトライ）
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except Exception:
    Retry = None

_TRANSLATE_SESSION = requests.Session()
# Cloudflare は非ブラウザ UA をブロックすることがあるためブラウザ型 UA を付与
_TRANSLATE_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  f"GRAPRO-Translator/{VERSION}",
})
if Retry is not None:
    try:
        _retry = Retry(total=2, backoff_factor=0.5,
                       status_forcelist=(500, 502, 503, 504),
                       allowed_methods=frozenset(["GET", "POST"]))
    except TypeError:  # 古い urllib3 は method_whitelist
        _retry = Retry(total=2, backoff_factor=0.5,
                       status_forcelist=(500, 502, 503, 504),
                       method_whitelist=frozenset(["GET", "POST"]))
    _adapter = HTTPAdapter(max_retries=_retry, pool_maxsize=NUM_WORKERS + 2)
    _TRANSLATE_SESSION.mount("https://", _adapter)
    _TRANSLATE_SESSION.mount("http://", _adapter)

# --- DeepL 言語コード変換 ---
# langdetect → DeepL ソース言語コード
_DEEPL_SOURCE_MAP = {
    "zh-cn": "ZH", "zh-tw": "ZH", "zh": "ZH",
    "en": "EN", "ja": "JA", "ko": "KO", "de": "DE", "fr": "FR",
    "es": "ES", "pt": "PT", "pt-br": "PT", "it": "IT", "nl": "NL",
    "pl": "PL", "ru": "RU", "bg": "BG", "cs": "CS", "da": "DA",
    "el": "EL", "et": "ET", "fi": "FI", "hu": "HU", "id": "ID",
    "lv": "LV", "lt": "LT", "nb": "NB", "ro": "RO", "sk": "SK",
    "sl": "SL", "sv": "SV", "tr": "TR", "uk": "UK", "ar": "AR",
}
# DeepL ターゲット言語コード（ターゲットは地域付きが必要な場合あり）
_DEEPL_TARGET_MAP = {
    "ja": "JA", "en": "EN-US", "zh": "ZH-HANS",
    "pt": "PT-PT", "pt-br": "PT-BR",
}


def _translate_libretranslate(text, source_lang):
    """LibreTranslate で翻訳。source="auto" 対応。
    戻り値: (translated_text, detected_lang)
    """
    resp = _TRANSLATE_SESSION.post(LIBRETRANSLATE_URL, json={
        "q": text, "source": source_lang or "auto", "target": TARGET_LANG,
        "format": "text", "api_key": LIBRETRANSLATE_API_KEY,
    }, timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        detected = source_lang
        dl = data.get("detectedLanguage")
        if isinstance(dl, dict):
            detected = dl.get("language", source_lang)
        return data.get("translatedText", text), detected
    raise RuntimeError(f"LibreTranslate HTTP {resp.status_code}: {resp.text[:200]}")


def _translate_deepl(text, source_lang):
    """DeepL API で翻訳"""
    tgt = _DEEPL_TARGET_MAP.get(TARGET_LANG, TARGET_LANG.upper())
    headers = {"Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}"}
    data = {"text": text, "target_lang": tgt}
    # DeepL対応言語ならsource_lang指定、非対応ならDeepLに自動検出させる
    src = _DEEPL_SOURCE_MAP.get(source_lang)
    if src:
        data["source_lang"] = src
    resp = _TRANSLATE_SESSION.post(DEEPL_API_URL, headers=headers, data=data, timeout=5)
    if resp.status_code == 200:
        translations = resp.json().get("translations", [])
        if translations:
            return translations[0].get("text", text)
    raise RuntimeError(f"DeepL HTTP {resp.status_code}: {resp.text[:200]}")


def _translate_grapro(text, source_lang):
    """GRAPRO中継サーバー経由で翻訳（Azure/LLMはサーバー側で処理）
    戻り値: (translated_text, detected_lang)
    """
    global _server_notification
    payload = {"text": text, "target": TARGET_LANG, "worker_id": CLIENT_ID}
    resp = _TRANSLATE_SESSION.post(GRAPRO_TRANSLATE_URL, json=payload, timeout=8)
    if resp.status_code == 200:
        data = resp.json()
        translated = data.get("translatedText", text)
        detected = data.get("detectedLanguage", "")
        # サーバーからの警告メッセージ
        if "warning" in data:
            _server_notification = {"type": "warn", "message": data["warning"]}
            print(f"[GRAPRO] サーバー警告: {data['warning']}")
        return translated, detected
    elif resp.status_code == 429:
        msg = resp.json().get("message", "リクエスト上限に達しました")
        _server_notification = {"type": "rate_limit", "message": msg}
        print(f"[GRAPRO] レート制限: {msg}")
        return text, source_lang
    elif resp.status_code == 403:
        msg = resp.json().get("message", "このクライアントはブロックされています")
        _server_notification = {"type": "blocked", "message": msg}
        print(f"[GRAPRO] ブロック: {msg}")
        return text, source_lang
    raise RuntimeError(f"GRAPRO HTTP {resp.status_code}: {resp.text[:200]}")


# エンジン名 → 翻訳関数のマッピング
_ENGINES = {
    "grapro":         _translate_grapro,
    "libretranslate": _translate_libretranslate,
    "deepl":          _translate_deepl,
}


_last_translate_error = None          # 直近のエラー（診断用）
_server_notification = None           # サーバーからの通知 {"type": "warn"|"rate_limit"|"blocked", "message": "..."}

# 翻訳結果キャッシュ（"GG" "ｗｗｗ" 等の頻出コメント再翻訳を防いでAPIコスト削減）
_TRANS_CACHE_MAX = 500
_trans_cache = OrderedDict()          # text -> (translated, detected_lang)
_trans_cache_lock = threading.Lock()

def translate_text(text, source_lang):
    """翻訳API に投げる。成功結果は LRU キャッシュする。
    戻り値: (translated_text, detected_lang)
    """
    global _last_translate_error
    # キャッシュヒット（エンジン切替時の混在を避けるためエンジン名込みでキー化）
    cache_key = (TRANSLATE_ENGINE, text)
    with _trans_cache_lock:
        hit = _trans_cache.get(cache_key)
        if hit is not None:
            _trans_cache.move_to_end(cache_key)
            return hit
    engine_fn = _ENGINES.get(TRANSLATE_ENGINE, _translate_grapro)
    try:
        result = engine_fn(text, source_lang)
        _last_translate_error = None
        # tuple なら (text, detected_lang)、str なら検出言語なし
        if isinstance(result, tuple):
            translated, detected = result
        else:
            translated, detected = result, source_lang
        # 成功時のみキャッシュ（レート制限等で原文が返ったケースは除外）
        if translated != text:
            with _trans_cache_lock:
                _trans_cache[cache_key] = (translated, detected)
                _trans_cache.move_to_end(cache_key)
                while len(_trans_cache) > _TRANS_CACHE_MAX:
                    _trans_cache.popitem(last=False)
        return translated, detected
    except Exception as e:
        _last_translate_error = str(e)
        print(f"[翻訳エラー][{TRANSLATE_ENGINE}] {e}")
        # GRAPRO 失敗時は LibreTranslate へ自動フォールバック（原文表示よりマシ）
        if engine_fn is _translate_grapro:
            try:
                fb, detected = _translate_libretranslate(text, source_lang or "auto")
                print("[翻訳フォールバック] LibreTranslate で翻訳成功")
                return fb, detected
            except Exception as e2:
                print(f"[翻訳フォールバック失敗] {e2}")
    return text, source_lang


# 絵文字・記号のみの文字列を検出するパターン
_EMOJI_ONLY = _re.compile(
    r'^[\s'
    r'\U0001F000-\U0001FFFF'  # 絵文字
    r'\U00002600-\U000027BF'  # その他記号
    r'\U0000FE00-\U0000FE0F'  # variation selectors
    r'\U00002300-\U000023FF'  # 時計・矢印等
    r'\U0001FA00-\U0001FFFF'  # 追加絵文字
    r'！-～'                   # 全角記号（！～）
    r'Ａ-Ｚａ-ｚ０-９'         # 全角英数（ｗｗｗ等）
    r'ー〜・。、！？'           # 全角句読点
    r'w笑草'                   # 半角w・笑・草
    r']+$'
)

def detect_language(text):
    """基本フィルタのみ。言語検出は翻訳API側に委任。
    Returns: "auto" (翻訳APIに投げる) / TARGET_LANG (翻訳不要・表示のみ) / None (表示しない)
    """
    stripped = text.strip()
    if not stripped:
        return None
    # 短いコメント（"GG" 等）は翻訳せず原文のまま表示（以前は非表示だった）
    if len(stripped) < MIN_CHARS:
        return TARGET_LANG
    # 絵文字・記号のみのコメントは翻訳不要（日本語扱いにして表示だけする）
    if _EMOJI_ONLY.match(stripped):
        return TARGET_LANG
    # 言語検出は翻訳API（Azure等）に任せる
    return "auto"


# ===== 翻訳ワーカー =====

def translation_worker():
    """
    キューからメッセージを取り出して翻訳し chat_messages に追加。
    NUM_WORKERS 本並列で動作するため複数人同時も詰まらない。
    """
    while not stop_event.is_set():
        try:
            item = translation_q.get(timeout=1)
        except queue.Empty:
            continue

        try:
            author   = item["author"]
            message  = item["message"]
            lang     = item["lang"]
            imageUrl = item.get("imageUrl", "")
            badgeUrl = item.get("badgeUrl", "")
            isMember = item.get("isMember", False)
            isMod    = item.get("isMod", False)
            isOwner  = item.get("isOwner", False)
            # Twitch拡張フィールド
            extra = {k: item.get(k) for k in
                     ("isVip","twitchColor","subMonths","isFirstMsg",
                      "isNotice","noticeType","noticeMsg") if k in item}

            base = {"id": next(_MSG_COUNTER),
                    "author": author, "imageUrl": imageUrl, "badgeUrl": badgeUrl,
                    "lang": lang, "isMember": isMember, "isMod": isMod,
                    "isOwner": isOwner, **extra}

            if item.get("isNotice"):
                # 通知メッセージはそのまま表示（翻訳不要）
                entry = {**base, "original": message, "translated": None}
            elif lang == TARGET_LANG:
                # 絵文字のみ等、ローカルで日本語確定 → 翻訳不要
                entry = {**base, "original": message, "translated": None}
            else:
                # lang="auto" → 翻訳APIに言語検出ごと丸投げ
                translated, detected = translate_text(message, lang)
                # APIが日本語と判定 → 翻訳不要（原文表示）
                if detected == TARGET_LANG:
                    entry = {**base, "original": message, "translated": None,
                             "lang": detected}
                else:
                    entry = {**base, "original": message, "translated": translated,
                             "lang": detected if detected else lang}

            # 棒読みちゃん読み上げ（翻訳後=日本語 or 原文=日本語）
            _bouyomi_text = entry.get("translated") or entry.get("original", "")
            if _bouyomi_text and not item.get("isNotice"):
                threading.Thread(target=_send_bouyomi,
                                 args=(_bouyomi_text,), daemon=True).start()

            with messages_lock:
                chat_messages.insert(0, entry)
                if len(chat_messages) > MAX_MESSAGES:
                    chat_messages.pop()
        except Exception as e:
            print(f"[翻訳ワーカー エラー] {e}", flush=True)

        translation_q.task_done()


# ===== チャット取得スレッド =====

_YT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
}
_YT_SESSION = requests.Session()
_YT_SESSION.headers.update(_YT_HEADERS)


def _get_initial_chat_info(video_id):
    """動画ページからcontinuationトークンとAPIキーを取得"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    resp = _YT_SESSION.get(url, timeout=10)
    html = resp.text
    # APIキー
    m = _re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
    api_key = m.group(1) if m else "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
    # continuationトークン（ライブチャット用）
    conts = _re.findall(r'"continuation":"([^"]+)"', html)
    if not conts:
        return None, None
    return api_key, conts[0]


def _poll_live_chat(api_key, continuation):
    """YouTube live_chat APIを1回ポーリングし (messages, next_continuation, timeout_ms) を返す"""
    url = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={api_key}"
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20260324.00.00",
            }
        },
        "continuation": continuation,
    }
    resp = _YT_SESSION.post(url, json=payload, timeout=10)
    data = resp.json()

    messages = []
    next_cont = None
    timeout_ms = 5000  # デフォルト5秒

    cc = data.get("continuationContents", {}).get("liveChatContinuation", {})

    # 次のcontinuation & ポーリング間隔
    for c in cc.get("continuations", []):
        icd = c.get("invalidationContinuationData") or c.get("timedContinuationData") or c.get("reloadContinuationData")
        if icd:
            next_cont = icd.get("continuation", next_cont)
            timeout_ms = icd.get("timeoutMs", timeout_ms)

    # メッセージ抽出
    for action in cc.get("actions", []):
        item = action.get("addChatItemAction", {}).get("item", {})
        renderer = item.get("liveChatTextMessageRenderer")
        if not renderer:
            continue
        # テキスト
        runs = renderer.get("message", {}).get("runs", [])
        text = "".join(r.get("text", "") for r in runs)
        if not text:
            continue
        # 著者情報
        author_name = renderer.get("authorName", {}).get("simpleText", "???")
        # アバター
        thumbs = renderer.get("authorPhoto", {}).get("thumbnails", [])
        image_url = thumbs[-1]["url"] if thumbs else ""
        # バッジ
        badge_url = ""
        is_member = False
        is_mod = False
        is_owner = False
        for badge in renderer.get("authorBadges", []):
            br = badge.get("liveChatAuthorBadgeRenderer", {})
            badge_type = br.get("customThumbnail")
            if badge_type:
                bt = badge_type.get("thumbnails", [])
                badge_url = bt[-1]["url"] if bt else ""
                is_member = True
            icon_type = br.get("icon", {}).get("iconType", "")
            if icon_type == "MODERATOR":
                is_mod = True
            elif icon_type == "OWNER":
                is_owner = True

        messages.append({
            "author":   author_name,
            "message":  text,
            "imageUrl": image_url,
            "badgeUrl": badge_url,
            "isMember": is_member,
            "isMod":    is_mod,
            "isOwner":  is_owner,
        })

    return messages, next_cont, timeout_ms


def _youtube_chat_worker(video_id):
    """YouTube Live Chat API で直接チャット取得 → 言語判定 → 翻訳キューへ投入"""
    print(f"[YouTube] video_id={video_id}")
    retry = 0
    while not stop_event.is_set():
        try:
            api_key, continuation = _get_initial_chat_info(video_id)
            if not continuation:
                print(f"[YouTube] continuationトークン取得失敗 retry={retry+1}")
                retry += 1
                _reconnect_wait(retry)
                continue
            print(f"[YouTube] 接続成功 retry={retry}")
            retry = 0
            while continuation and not stop_event.is_set():
                msgs, next_cont, wait_ms = _poll_live_chat(api_key, continuation)
                for m in msgs:
                    lang = detect_language(m["message"])
                    if lang is None:
                        continue
                    m["lang"] = lang
                    _enqueue_translation(m)
                if next_cont:
                    continuation = next_cont
                else:
                    print("[YouTube] continuation終了（配信終了?）")
                    break
                sleep_sec = max(0.5, min(wait_ms / 1000, 10))
                waited = 0
                while waited < sleep_sec and not stop_event.is_set():
                    time.sleep(0.3)
                    waited += 0.3
        except Exception as e:
            print(f"[YouTube エラー] {e}")
            if not stop_event.is_set():
                retry += 1
                print(f"[YouTube] 再接続します retry={retry}")
                _reconnect_wait(retry)
    print("[YouTube 終了]")


# ===== Twitch チャット取得（匿名IRC） =====

def _twitch_chat_worker(channel):
    """Twitch IRC (justinfan匿名) でチャット取得 → 翻訳キューへ投入"""
    import random
    nick = f"justinfan{random.randint(10000, 99999)}"
    print(f"[Twitch] channel={channel} nick={nick}")
    retry = 0
    while not stop_event.is_set():
        sock = None
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(("irc.chat.twitch.tv", 6667))
            # IRCタグ + コマンド（サブスク/レイド通知等）を要求
            sock.send(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
            sock.send(f"NICK {nick}\r\n".encode("utf-8"))
            sock.send(f"JOIN #{channel.lower()}\r\n".encode("utf-8"))
            print(f"[Twitch] IRC接続成功 (tags+commands有効)")
            retry = 0
            buf = ""
            reconnect = False
            sock.settimeout(1)
            while not stop_event.is_set() and not reconnect:
                try:
                    data = sock.recv(4096).decode("utf-8", errors="replace")
                    if not data:
                        # サーバー側からの切断 → バックオフして再接続
                        print("[Twitch] サーバーから切断されました")
                        reconnect = True
                        break
                    buf += data
                    while "\r\n" in buf:
                        line, buf = buf.split("\r\n", 1)
                        # PING/PONG keepalive
                        if line.startswith("PING"):
                            sock.send(f"PONG {line[5:]}\r\n".encode("utf-8"))
                            continue
                        # サーバーからの再接続要求（メンテナンス等）
                        if line.startswith(":tmi.twitch.tv RECONNECT"):
                            print("[Twitch] サーバーから RECONNECT 要求")
                            reconnect = True
                            break
                        # タグをパース（共通処理）
                        tags_str = ""
                        rest = line
                        if line.startswith("@"):
                            sp = line.split(" ", 1)
                            if len(sp) == 2:
                                tags_str = sp[0][1:]  # '@'を除去
                                rest = sp[1]
                        tags = {}
                        if tags_str:
                            for kv in tags_str.split(";"):
                                if "=" in kv:
                                    k, v = kv.split("=", 1)
                                    tags[k] = v

                        # --- USERNOTICE: サブスク/レイド/ギフト通知 ---
                        um = _re.match(r'^:tmi\.twitch\.tv USERNOTICE #\S+(?: :(.+))?$', rest)
                        if um:
                            notice_type = tags.get("msg-id", "")
                            sys_msg = tags.get("system-msg", "").replace("\\s", " ")
                            user_msg = (um.group(1) or "").strip()
                            display_name = tags.get("display-name", "")
                            _enqueue_translation({
                                "author": display_name or notice_type,
                                "message": user_msg or sys_msg,
                                "lang": "en", "imageUrl": "", "badgeUrl": "",
                                "isMember": False, "isMod": False, "isOwner": False,
                                "isVip": False, "twitchColor": "",
                                "subMonths": 0, "isFirstMsg": False,
                                "isNotice": True, "noticeType": notice_type,
                                "noticeMsg": sys_msg,
                            })
                            continue

                        # --- PRIVMSG: 通常チャットメッセージ ---
                        m = _re.match(r'^:(\S+)!\S+ PRIVMSG #\S+ :(.+)$', rest)
                        if m:
                            author = m.group(1)
                            text = m.group(2).strip()
                            if not text:
                                continue
                            display_name = tags.get("display-name", author)
                            if display_name:
                                author = display_name
                            # バッジ解析
                            badges_raw = tags.get("badges", "")
                            badge_set = set()
                            if badges_raw:
                                for b in badges_raw.split(","):
                                    badge_set.add(b.split("/")[0])
                            is_sub = "subscriber" in badge_set or "founder" in badge_set
                            is_mod = "moderator" in badge_set
                            is_owner = "broadcaster" in badge_set
                            is_vip = "vip" in badge_set
                            # 追加情報
                            twitch_color = tags.get("color", "")
                            badge_info = tags.get("badge-info", "")
                            sub_months = 0
                            if badge_info:
                                for bi in badge_info.split(","):
                                    if bi.startswith("subscriber/") or bi.startswith("founder/"):
                                        try: sub_months = int(bi.split("/")[1])
                                        except: pass
                            is_first = tags.get("first-msg", "0") == "1"
                            lang = detect_language(text)
                            if lang is None:
                                continue
                            _enqueue_translation({
                                "author": author, "message": text,
                                "lang": lang, "imageUrl": "", "badgeUrl": "",
                                "isMember": is_sub, "isMod": is_mod, "isOwner": is_owner,
                                "isVip": is_vip, "twitchColor": twitch_color,
                                "subMonths": sub_months, "isFirstMsg": is_first,
                                "isNotice": False, "noticeType": "", "noticeMsg": "",
                            })
                except _socket.timeout:
                    continue
            # 正常切断/RECONNECT 要求後もバックオフを挟む（即時再接続の連打防止）
            if not stop_event.is_set():
                retry += 1
                print(f"[Twitch] 再接続します retry={retry}")
                _reconnect_wait(retry)
        except Exception as e:
            print(f"[Twitch エラー] {e}")
            if not stop_event.is_set():
                retry += 1
                print(f"[Twitch] 再接続します retry={retry}")
                _reconnect_wait(retry)
        finally:
            if sock:
                try: sock.close()
                except: pass
    print("[Twitch 終了]")


# ===== ツイキャス コメント取得（API v2） =====

# ツイキャスAPI設定
_TWITCASTING_CLIENT_ID     = os.environ.get("TWITCASTING_CLIENT_ID", "")
_TWITCASTING_CLIENT_SECRET = os.environ.get("TWITCASTING_CLIENT_SECRET", "")
_TWITCASTING_TOKEN_FILE    = os.path.join(BASE_DIR, "twitcasting_token.json")

def _twitcasting_get_token():
    """保存済みトークンを読み込む。なければ None"""
    try:
        if os.path.exists(_TWITCASTING_TOKEN_FILE):
            with open(_TWITCASTING_TOKEN_FILE, "r") as f:
                return _json.load(f).get("access_token")
    except:
        pass
    return None

def _twitcasting_save_token(token):
    """トークンをファイルに保存"""
    with open(_TWITCASTING_TOKEN_FILE, "w") as f:
        _json.dump({"access_token": token}, f)

def _twitcasting_fetch_token():
    """Client Credentials フローで新規トークンを取得・保存。失敗時 None"""
    if not (_TWITCASTING_CLIENT_ID and _TWITCASTING_CLIENT_SECRET):
        return None
    try:
        import base64
        cred = base64.b64encode(
            f"{_TWITCASTING_CLIENT_ID}:{_TWITCASTING_CLIENT_SECRET}".encode()
        ).decode()
        r = requests.post("https://apiv2.twitcasting.tv/oauth2/access_token",
                          headers={"Authorization": f"Basic {cred}"},
                          data={"grant_type": "client_credentials"}, timeout=10)
        if r.ok:
            token = r.json().get("access_token")
            if token:
                _twitcasting_save_token(token)
                return token
    except Exception as e:
        print(f"[ツイキャス] トークン取得失敗: {e}")
    return None

def _twitcasting_delete_token():
    """失効トークンのファイルを破棄"""
    try:
        if os.path.exists(_TWITCASTING_TOKEN_FILE):
            os.remove(_TWITCASTING_TOKEN_FILE)
    except:
        pass

def _twitcasting_get_movie_id(user_id, token):
    """ユーザーの現在のライブ配信IDを取得。
    戻り値: (movie_id or None, unauthorized: bool)"""
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"https://apiv2.twitcasting.tv/users/{user_id}/current_live",
                     headers=headers, timeout=10)
    if r.ok:
        data = r.json()
        movie = data.get("movie", {})
        return str(movie.get("id", "")), False
    return None, r.status_code == 401

def _twitcasting_system_message(text, notice):
    """オーバーレイにシステム通知を1件表示"""
    with messages_lock:
        chat_messages.insert(0, {
            "id": next(_MSG_COUNTER),
            "author": "システム",
            "original": text,
            "translated": None,
            "lang": "ja", "imageUrl": "", "badgeUrl": "",
            "isMember": False, "isMod": False, "isOwner": False,
            "isNotice": True, "noticeType": "system",
            "noticeMsg": notice,
        })


def _twitcasting_chat_worker(user_id):
    """ツイキャス API v2 でコメント取得 → 翻訳キューへ投入"""
    token = _twitcasting_get_token() or _twitcasting_fetch_token()
    if not token:
        print("[ツイキャス] APIトークンが未設定です。環境変数 TWITCASTING_CLIENT_ID / TWITCASTING_CLIENT_SECRET を設定してください")
        _twitcasting_system_message(
            "⚠ ツイキャスのAPI設定が必要です（設定 → 詳細はドキュメント参照）",
            "ツイキャスAPI未設定: ClientID/SecretをGRAPRO設定で登録してください")
        return

    print(f"[ツイキャス] user_id={user_id}")
    retry = 0
    while not stop_event.is_set():
        try:
            movie_id, unauthorized = _twitcasting_get_movie_id(user_id, token)
            if unauthorized:
                # トークン失効 → 破棄して再取得（旧実装は失効後に無限リトライしていた）
                print("[ツイキャス] トークン失効を検出 → 再取得します")
                _twitcasting_delete_token()
                token = _twitcasting_fetch_token()
                if not token:
                    _twitcasting_system_message(
                        "⚠ ツイキャスAPIトークンの再取得に失敗しました",
                        "ツイキャスAPI認証失敗: ClientID/Secretを確認してください")
                    return
                continue
            if not movie_id:
                print(f"[ツイキャス] ライブ配信が見つかりません retry={retry+1}")
                retry += 1
                _reconnect_wait(retry)
                continue
            print(f"[ツイキャス] movie_id={movie_id}")
            retry = 0
            last_id = 0
            headers = {"Authorization": f"Bearer {token}"}
            while not stop_event.is_set():
                params = {"limit": 50}
                if last_id:
                    params["slice_id"] = last_id
                r = requests.get(f"https://apiv2.twitcasting.tv/movies/{movie_id}/comments",
                                 headers=headers, params=params, timeout=10)
                if r.status_code == 401:
                    print("[ツイキャス] コメント取得中にトークン失効 → 再取得")
                    _twitcasting_delete_token()
                    token = _twitcasting_fetch_token()
                    break
                if not r.ok:
                    print(f"[ツイキャス] API応答エラー status={r.status_code}")
                    break
                data = r.json()
                comments = data.get("comments", [])
                # 古い順に処理（APIは降順で返すので反転）
                for c in reversed(comments):
                    cid = c.get("id", 0)
                    if cid <= last_id:
                        continue
                    last_id = cid
                    author = c.get("from_user", {}).get("name", "???")
                    text = c.get("message", "").strip()
                    image_url = c.get("from_user", {}).get("image", "")
                    if not text:
                        continue
                    lang = detect_language(text)
                    if lang is None:
                        continue
                    _enqueue_translation({
                        "author": author, "message": text,
                        "lang": lang, "imageUrl": image_url, "badgeUrl": "",
                        "isMember": False, "isMod": False, "isOwner": False,
                    })
                # ポーリング間隔（APIレート制限考慮: 2秒）
                waited = 0
                while waited < 2 and not stop_event.is_set():
                    time.sleep(0.3)
                    waited += 0.3
        except Exception as e:
            print(f"[ツイキャス エラー] {e}")
            if not stop_event.is_set():
                retry += 1
                print(f"[ツイキャス] 再接続します retry={retry}")
                _reconnect_wait(retry)
    print("[ツイキャス 終了]")


# ===== プラットフォーム判定 & ディスパッチ =====

def _detect_platform(url_or_id):
    """URLからプラットフォームと識別子を判定
    Returns: (platform, identifier)
      platform: "youtube" | "twitch" | "twitcasting" | "demo" | "unknown"
    """
    s = url_or_id.strip()
    if s.upper().startswith("DEMO"):
        return "demo", s
    # Twitch
    m = _re.match(r'(?:https?://)?(?:www\.)?twitch\.tv/(\w+)', s)
    if m:
        return "twitch", m.group(1)
    # ツイキャス
    m = _re.match(r'(?:https?://)?(?:www\.)?twitcasting\.tv/(\w+)', s)
    if m:
        return "twitcasting", m.group(1)
    # YouTube
    if "youtube.com" in s or "youtu.be" in s:
        if "v=" in s:
            vid = s.split("v=")[-1].split("&")[0]
            return "youtube", vid
        elif "youtu.be/" in s:
            vid = s.split("youtu.be/")[-1].split("?")[0]
            return "youtube", vid
    # video_idと仮定
    if len(s) >= 4:
        return "youtube", s
    return "unknown", s


def chat_worker(video_id):
    """プラットフォーム判定して適切なワーカーにディスパッチ"""
    platform, identifier = _detect_platform(video_id)
    print(f"[チャット開始] platform={platform} id={identifier}")
    if platform == "youtube":
        _youtube_chat_worker(identifier)
    elif platform == "twitch":
        _twitch_chat_worker(identifier)
    elif platform == "twitcasting":
        _twitcasting_chat_worker(identifier)
    elif platform == "demo":
        return  # DEMOはGUI側で処理
    else:
        print(f"[チャット] 不明なプラットフォーム: {video_id}")
    print("[チャット終了]")


# ===== アップデートチェック =====

_GITHUB_REPO = "yakizakanateisyoku/GRAPRO-TRANSLATOR"  # 公開リポジトリ
_latest_version = None  # 最新バージョン（チェック済み）

def _ver_tuple(v):
    """"1.4.1" → (1, 4, 1)。比較用"""
    try:
        return tuple(int(x) for x in _re.findall(r"\d+", v)[:3])
    except:
        return (0,)

def check_update():
    """GitHub Releases APIで最新バージョンを確認"""
    global _latest_version
    try:
        r = requests.get(f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
                         timeout=5, headers={"Accept": "application/vnd.github.v3+json"})
        if r.ok:
            tag = r.json().get("tag_name", "")
            latest = tag.lstrip("vV")
            # 単純な != 比較だと開発版(現行>公開)でも通知が出るため大小比較にする
            if latest and _ver_tuple(latest) > _ver_tuple(VERSION):
                _latest_version = latest
                print(f"[アップデート] 新しいバージョン v{latest} が利用可能です")
            else:
                _latest_version = None
    except:
        pass

@app.route('/update_check')
def update_check():
    """最新バージョン情報を返す"""
    return jsonify({
        "current": VERSION,
        "latest": _latest_version,
        "update_available": _latest_version is not None,
        "url": f"https://github.com/{_GITHUB_REPO}/releases/latest" if _latest_version else None,
    })


_watchdog_started = False

def _watchdog_loop():
    """30秒間隔でスレッド死活監視。死んだ翻訳ワーカー/チャットスレッドを自動再起動"""
    global chat_thread
    while True:
        time.sleep(30)
        try:
            # /stop 中（stop_event セット中）はワーカーが終了するのは正常動作
            if stop_event.is_set():
                continue
            # 翻訳ワーカーの再起動
            for i, t in enumerate(worker_threads):
                if not t.is_alive():
                    nt = threading.Thread(target=translation_worker, daemon=True,
                                          name=f"worker-{i}")
                    nt.start()
                    worker_threads[i] = nt
                    print(f"[watchdog] 翻訳ワーカー{i} が停止していたため再起動")
            # チャットスレッドの再起動（配信中に予期せず死んだ場合のみ）
            if _last_video_id and (chat_thread is None or not chat_thread.is_alive()):
                print(f"[watchdog] チャットスレッド停止を検出 → 再起動 ({_last_video_id})")
                chat_thread = threading.Thread(target=chat_worker,
                                               args=(_last_video_id,), daemon=True)
                chat_thread.start()
        except Exception as e:
            print(f"[watchdog] エラー: {e}")


def start_workers():
    """翻訳ワーカースレッドを NUM_WORKERS 本起動"""
    global worker_threads, _watchdog_started
    worker_threads = []
    for i in range(NUM_WORKERS):
        t = threading.Thread(target=translation_worker, daemon=True, name=f"worker-{i}")
        t.start()
        worker_threads.append(t)
    print(f"[ワーカー起動] {NUM_WORKERS} 本")
    # 死活監視 watchdog（多重起動防止）
    if not _watchdog_started:
        _watchdog_started = True
        threading.Thread(target=_watchdog_loop, daemon=True, name="watchdog").start()
    # バックグラウンドでアップデートチェック
    threading.Thread(target=check_update, daemon=True).start()


# ===== OBS オーバーレイ HTML =====
OVERLAY_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  :root{
    --body-bg:transparent;
    --msg-bg:rgba(0,0,0,0.88);
    --accent-translated:#29b6f6;
    --accent-japanese:#555;
    --author-color:#ffe066;
    --author-font:'Meiryo','Noto Sans JP',sans-serif;
    --author-size:14px;
    --text-color:#ffffff;
    --text-font:'Meiryo','Noto Sans JP',sans-serif;
    --text-size:18px;
    --original-color:#bbb;
  }
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--body-bg);font-family:var(--text-font);overflow-x:hidden}
  #messages{position:fixed;bottom:16px;left:16px;right:16px;display:flex;flex-direction:column-reverse;gap:6px;max-height:90vh;overflow:hidden}
  .msg{background:var(--msg-bg);border-radius:8px;padding:8px 14px;color:var(--text-color);font-family:var(--text-font);font-size:var(--text-size);font-weight:600;line-height:1.6;animation:fadein 0.35s ease;word-break:break-word;text-shadow:0 1px 3px rgba(0,0,0,0.8)}
  .msg.translated{border-left:4px solid var(--accent-translated)}
  .msg.japanese{border-left:4px solid var(--accent-japanese)}
  .meta{display:flex;align-items:center;gap:6px;margin-bottom:4px}
  .avatar{width:22px;height:22px;border-radius:50%;object-fit:cover;flex-shrink:0;border:1px solid rgba(255,255,255,0.3)}
  .author{color:var(--author-color);font-family:var(--author-font);font-weight:bold;font-size:var(--author-size)}
  .lang-badge{font-size:12px;background:var(--accent-translated);color:#003;border-radius:4px;padding:1px 7px;font-weight:bold;text-shadow:none !important;display:inline-block}
  .badge-member{font-size:12px;background:#2ecc71;color:#000;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-mod{font-size:12px;background:#5865f2;color:#fff;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-owner{font-size:12px;background:#f1c40f;color:#000;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-vip{font-size:12px;background:#e005b9;color:#fff;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-first{font-size:12px;background:#f97316;color:#fff;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-months{font-size:12px;background:#9b59b6;color:#fff;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .msg.notice{border-left:4px solid #e005b9;background:rgba(80,0,60,0.85)}
  .translated-text{color:var(--text-color);font-family:var(--text-font);font-size:var(--text-size);font-weight:600}
  .original{color:var(--original-color);font-size:13px;font-weight:600;margin-top:3px}
  @keyframes fadein{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  #hype-badge{position:fixed;top:8px;left:8px;background:rgba(20,20,25,0.85);border:1px solid #ff6b6b;border-radius:8px;padding:6px 14px;color:#fff;font-family:'Meiryo','Noto Sans JP',sans-serif;font-size:14px;font-weight:600;z-index:998;display:none;align-items:center;gap:8px;backdrop-filter:blur(4px)}
  #hype-badge.visible{display:flex}
  #hype-badge .hype-num{font-size:22px;font-weight:bold;color:#ff6b6b;font-variant-numeric:tabular-nums}
  #hype-badge .hype-label{font-size:12px;font-weight:600;color:#aaa}
  #gear{position:fixed;top:8px;right:8px;width:32px;height:32px;background:rgba(60,60,60,0.7);border:none;border-radius:50%;color:#ccc;font-size:18px;cursor:pointer;z-index:1000;display:flex;align-items:center;justify-content:center;transition:background 0.2s,transform 0.3s}
  #gear:hover{background:rgba(100,100,100,0.9);transform:rotate(45deg)}
  #panel{display:none;position:fixed;top:0;right:0;width:320px;height:100vh;background:rgba(20,20,25,0.96);z-index:999;overflow-y:auto;padding:16px;color:#ddd;font-family:'Meiryo','Noto Sans JP',sans-serif;font-size:13px;font-weight:600;border-left:1px solid #333}
  #panel.open{display:block}
  #panel h3{color:#fff;font-size:15px;margin:0 0 12px;border-bottom:1px solid #444;padding-bottom:6px}
  .sgroup{margin-bottom:14px}
  .sgroup label{display:block;color:#aaa;font-size:12px;margin-bottom:3px}
  .sgroup input[type=color]{width:40px;height:28px;border:1px solid #555;background:#222;cursor:pointer;vertical-align:middle}
  .sgroup input[type=range]{width:120px;vertical-align:middle}
  .sgroup select{background:#222;color:#ddd;border:1px solid #555;padding:3px 6px;font-size:12px}
  .sgroup .val{color:#888;font-size:12px;margin-left:4px}
  .srow{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  #panel .btn-row{display:flex;gap:8px;margin-top:12px}
  #panel button{background:#333;color:#ccc;border:1px solid #555;border-radius:4px;padding:5px 14px;font-size:12px;cursor:pointer}
  #panel button:hover{background:#444}
  #panel button.primary{background:#29b6f6;color:#000;border-color:#29b6f6}
  #panel button.primary:hover{background:#4fc3f7}
  .tabs{display:flex;gap:2px;margin-bottom:12px;border-bottom:1px solid #444;padding-bottom:0}
  .tab{background:#222;color:#888;border:none;border-radius:4px 4px 0 0;padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer}
  .tab.active{background:#29b6f6;color:#000;font-weight:bold}
  .tab-content{display:none}
  .tab-content.active{display:block}
  .toggle-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
  .toggle-row label{color:#ccc;font-size:12px}
  .toggle-row input[type=checkbox]{width:16px;height:16px;accent-color:#29b6f6}
</style></head><body>
<div id="hype-badge"><span style="font-size:18px">&#128293;</span><div><span class="hype-num" id="hypeNum">0</span><br><span class="hype-label">盛り上がり度数</span></div></div>
<button id="gear" title="設定">&#9881;</button>
<div id="panel">
  <h3>&#9881; オーバーレイ設定</h3>
  <div class="tabs">
    <button class="tab active" data-tab="tab-common">共通</button>
    <button class="tab" data-tab="tab-twitch">Twitch</button>
    <button class="tab" data-tab="tab-youtube">YouTube</button>
    <button class="tab" data-tab="tab-twitcas">ツイキャス</button>
  </div>
  <!-- 共通タブ -->
  <div id="tab-common" class="tab-content active">
  <div class="sgroup">
    <label>背景色（ブラウザ全体）</label>
    <div class="srow"><input type="color" id="s_bodyBg" value="#000000"><input type="text" id="s_bodyBgText" value="transparent" style="background:#222;color:#ddd;border:1px solid #555;width:130px;padding:2px 4px;font-size:12px"> <span class="val">※ "transparent" で透過</span></div>
  </div>
  <div class="sgroup">
    <label>コメント背景色</label>
    <div class="srow"><input type="color" id="s_msgBg" value="#000000"><input type="text" id="s_msgBgText" value="rgba(0,0,0,0.88)" style="background:#222;color:#ddd;border:1px solid #555;width:160px;padding:2px 4px;font-size:12px"></div>
  </div>
  <div class="sgroup">
    <label>アクセント（翻訳済み）</label>
    <div class="srow"><input type="color" id="s_accentTranslated" value="#29b6f6"></div>
  </div>
  <div class="sgroup">
    <label>アクセント（日本語）</label>
    <div class="srow"><input type="color" id="s_accentJapanese" value="#555555"></div>
  </div>
  <div class="sgroup">
    <label>リスナー名 色</label>
    <div class="srow"><input type="color" id="s_authorColor" value="#ffe066"></div>
  </div>
  <div class="sgroup">
    <label>リスナー名 フォント</label>
    <div class="srow"><select id="s_authorFont">
      <option value="Meiryo, Noto Sans JP, sans-serif">メイリオ</option>
      <option value="Yu Gothic, sans-serif">游ゴシック</option>
      <option value="Noto Sans JP, sans-serif">Noto Sans JP</option>
      <option value="MS Gothic, monospace">MS ゴシック</option>
      <option value="BIZ UDGothic, sans-serif">BIZ UDゴシック</option>
      <option value="Arial, sans-serif">Arial</option>
      <option value="Segoe UI, sans-serif">Segoe UI</option>
    </select></div>
  </div>
  <div class="sgroup">
    <label>リスナー名 サイズ</label>
    <div class="srow"><input type="range" id="s_authorSize" min="10" max="30" value="14"><span class="val" id="v_authorSize">14px</span></div>
  </div>
  <div class="sgroup">
    <label>コメント文字色</label>
    <div class="srow"><input type="color" id="s_textColor" value="#ffffff"></div>
  </div>
  <div class="sgroup">
    <label>コメント フォント</label>
    <div class="srow"><select id="s_textFont">
      <option value="Meiryo, Noto Sans JP, sans-serif">メイリオ</option>
      <option value="Yu Gothic, sans-serif">游ゴシック</option>
      <option value="Noto Sans JP, sans-serif">Noto Sans JP</option>
      <option value="MS Gothic, monospace">MS ゴシック</option>
      <option value="BIZ UDGothic, sans-serif">BIZ UDゴシック</option>
      <option value="Arial, sans-serif">Arial</option>
      <option value="Segoe UI, sans-serif">Segoe UI</option>
    </select></div>
  </div>
  <div class="sgroup">
    <label>コメント サイズ</label>
    <div class="srow"><input type="range" id="s_textSize" min="12" max="36" value="18"><span class="val" id="v_textSize">18px</span></div>
  </div>
  <div class="sgroup">
    <label>原文の色</label>
    <div class="srow"><input type="color" id="s_originalColor" value="#bbbbbb"></div>
  </div>
  <div class="sgroup">
    <label>表示件数</label>
    <div class="srow"><input type="range" id="s_count" min="1" max="20" value="5"><span class="val" id="v_count">5</span></div>
  </div>
  <div class="sgroup" style="border-top:1px solid #444;padding-top:10px;margin-top:10px">
    <label>SHOWROOM 盛り上がり度数</label>
    <div class="srow"><select id="s_showroomHype"><option value="off">非表示</option><option value="on">表示</option></select></div>
  </div>
  </div>
  <!-- Twitchタブ -->
  <div id="tab-twitch" class="tab-content">
  <div class="toggle-row"><label>ユーザー名にTwitchの色を使う</label><input type="checkbox" id="s_twUseTwitchColor" checked></div>
  <div class="toggle-row"><label>サブスク月数を表示</label><input type="checkbox" id="s_twShowSubMonths" checked></div>
  <div class="toggle-row"><label>初コメバッジを表示</label><input type="checkbox" id="s_twShowFirstMsg" checked></div>
  <div class="toggle-row"><label>VIPバッジを表示</label><input type="checkbox" id="s_twShowVip" checked></div>
  <div class="toggle-row"><label>サブスク/レイド/ギフト通知を表示</label><input type="checkbox" id="s_twShowNotices" checked></div>
  </div>
  <!-- YouTubeタブ -->
  <div id="tab-youtube" class="tab-content">
  <div class="toggle-row"><label>アバター画像を表示</label><input type="checkbox" id="s_ytShowAvatar" checked></div>
  <div class="toggle-row"><label>メンバーバッジを表示</label><input type="checkbox" id="s_ytShowMember" checked></div>
  <div class="toggle-row"><label>モデレーターバッジを表示</label><input type="checkbox" id="s_ytShowMod" checked></div>
  </div>
  <!-- ツイキャスタブ -->
  <div id="tab-twitcas" class="tab-content">
  <p style="color:#888;font-size:12px;margin-top:8px">ツイキャス固有の設定は今後追加予定です。</p>
  </div>
  <div class="btn-row">
    <button class="primary" id="btnSave">保存</button>
    <button id="btnReset">リセット</button>
    <button id="btnClose">閉じる</button>
  </div>
</div>
<div id="messages"></div>
<script>
const TOKEN='{{ token }}';   /* 設定保存等の状態変更APIに必要（サーバーが埋め込む） */
const R=document.documentElement.style;
function applyCSS(s){
  R.setProperty('--body-bg',s.bodyBg||'transparent');
  R.setProperty('--msg-bg',s.msgBg||'rgba(0,0,0,0.88)');
  R.setProperty('--accent-translated',s.accentTranslated||'#29b6f6');
  R.setProperty('--accent-japanese',s.accentJapanese||'#555');
  R.setProperty('--author-color',s.authorColor||'#ffe066');
  R.setProperty('--author-font',s.authorFont||"'Meiryo','Noto Sans JP',sans-serif");
  R.setProperty('--author-size',(s.authorSize||14)+'px');
  R.setProperty('--text-color',s.textColor||'#ffffff');
  R.setProperty('--text-font',s.textFont||"'Meiryo','Noto Sans JP',sans-serif");
  R.setProperty('--text-size',(s.textSize||18)+'px');
  R.setProperty('--original-color',s.originalColor||'#bbb');
}
function fillForm(s){
  document.getElementById('s_bodyBgText').value=s.bodyBg||'transparent';
  document.getElementById('s_msgBgText').value=s.msgBg||'rgba(0,0,0,0.88)';
  document.getElementById('s_accentTranslated').value=toHex(s.accentTranslated||'#29b6f6');
  document.getElementById('s_accentJapanese').value=toHex(s.accentJapanese||'#555555');
  document.getElementById('s_authorColor').value=toHex(s.authorColor||'#ffe066');
  document.getElementById('s_authorFont').value=s.authorFont||'Meiryo, Noto Sans JP, sans-serif';
  document.getElementById('s_authorSize').value=s.authorSize||14;
  document.getElementById('v_authorSize').textContent=(s.authorSize||14)+'px';
  document.getElementById('s_textColor').value=toHex(s.textColor||'#ffffff');
  document.getElementById('s_textFont').value=s.textFont||'Meiryo, Noto Sans JP, sans-serif';
  document.getElementById('s_textSize').value=s.textSize||18;
  document.getElementById('v_textSize').textContent=(s.textSize||18)+'px';
  document.getElementById('s_originalColor').value=toHex(s.originalColor||'#bbbbbb');
  document.getElementById('s_count').value=s.count||5;
  document.getElementById('v_count').textContent=s.count||5;
  // Twitch
  document.getElementById('s_twUseTwitchColor').checked=s.twUseTwitchColor!==false;
  document.getElementById('s_twShowSubMonths').checked=s.twShowSubMonths!==false;
  document.getElementById('s_twShowFirstMsg').checked=s.twShowFirstMsg!==false;
  document.getElementById('s_twShowVip').checked=s.twShowVip!==false;
  document.getElementById('s_twShowNotices').checked=s.twShowNotices!==false;
  // YouTube
  document.getElementById('s_ytShowAvatar').checked=s.ytShowAvatar!==false;
  document.getElementById('s_ytShowMember').checked=s.ytShowMember!==false;
  document.getElementById('s_ytShowMod').checked=s.ytShowMod!==false;
  document.getElementById('s_showroomHype').value=s.showroomHype||'off';
  applyHypeVisibility(s.showroomHype||'off');
}
function toHex(c){
  if(/^#[0-9a-f]{6}$/i.test(c))return c;
  if(/^#[0-9a-f]{3}$/i.test(c))return '#'+c[1]+c[1]+c[2]+c[2]+c[3]+c[3];
  const d=document.createElement('div');d.style.color=c;document.body.appendChild(d);
  const m=getComputedStyle(d).color.match(/\d+/g);d.remove();
  if(!m)return'#000000';
  return'#'+m.slice(0,3).map(x=>(+x).toString(16).padStart(2,'0')).join('');
}
// タブ切り替え
document.querySelectorAll('.tab').forEach(t=>{
  t.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById(t.dataset.tab).classList.add('active');
  });
});
function readForm(){
  return{
    bodyBg:document.getElementById('s_bodyBgText').value.trim()||'transparent',
    msgBg:document.getElementById('s_msgBgText').value.trim()||'rgba(0,0,0,0.88)',
    accentTranslated:document.getElementById('s_accentTranslated').value,
    accentJapanese:document.getElementById('s_accentJapanese').value,
    authorColor:document.getElementById('s_authorColor').value,
    authorFont:document.getElementById('s_authorFont').value,
    authorSize:document.getElementById('s_authorSize').value,
    textColor:document.getElementById('s_textColor').value,
    textFont:document.getElementById('s_textFont').value,
    textSize:document.getElementById('s_textSize').value,
    originalColor:document.getElementById('s_originalColor').value,
    count:document.getElementById('s_count').value,
    showroomHype:document.getElementById('s_showroomHype').value,
    // Twitch設定
    twUseTwitchColor:document.getElementById('s_twUseTwitchColor').checked,
    twShowSubMonths:document.getElementById('s_twShowSubMonths').checked,
    twShowFirstMsg:document.getElementById('s_twShowFirstMsg').checked,
    twShowVip:document.getElementById('s_twShowVip').checked,
    twShowNotices:document.getElementById('s_twShowNotices').checked,
    // YouTube設定
    ytShowAvatar:document.getElementById('s_ytShowAvatar').checked,
    ytShowMember:document.getElementById('s_ytShowMember').checked,
    ytShowMod:document.getElementById('s_ytShowMod').checked,
  };
}
function liveUpdate(){const f=readForm();applyCSS(f);applyHypeVisibility(f.showroomHype);}
document.querySelectorAll('#panel input, #panel select').forEach(el=>{
  el.addEventListener('input',()=>{
    if(el.id==='s_authorSize')document.getElementById('v_authorSize').textContent=el.value+'px';
    if(el.id==='s_textSize')document.getElementById('v_textSize').textContent=el.value+'px';
    if(el.id==='s_count'){document.getElementById('v_count').textContent=el.value;maxShow=+el.value;}
    liveUpdate();
  });
});
const panel=document.getElementById('panel');
document.getElementById('gear').addEventListener('click',()=>panel.classList.toggle('open'));
document.getElementById('btnClose').addEventListener('click',()=>panel.classList.remove('open'));
document.getElementById('btnSave').addEventListener('click',async()=>{
  const s=readForm();
  CFG=Object.assign({},DEFAULTS,s);
  try{
    await fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Grapro-Token':TOKEN},body:JSON.stringify(s)});
    document.getElementById('btnSave').textContent='\u4fdd\u5b58\u3057\u307e\u3057\u305f!';
    setTimeout(()=>document.getElementById('btnSave').textContent='\u4fdd\u5b58',1500);
  }catch(e){alert('\u4fdd\u5b58\u5931\u6557: '+e);}
});
const DEFAULTS={bodyBg:'transparent',msgBg:'rgba(0,0,0,0.88)',accentTranslated:'#29b6f6',accentJapanese:'#555555',authorColor:'#ffe066',authorFont:'Meiryo, Noto Sans JP, sans-serif',authorSize:'14',textColor:'#ffffff',textFont:'Meiryo, Noto Sans JP, sans-serif',textSize:'18',originalColor:'#bbbbbb',count:'5',twUseTwitchColor:true,twShowSubMonths:true,twShowFirstMsg:true,twShowVip:true,twShowNotices:true,ytShowAvatar:true,ytShowMember:true,ytShowMod:true,showroomHype:'off'};
let CFG=Object.assign({},DEFAULTS);
document.getElementById('btnReset').addEventListener('click',()=>{fillForm(DEFAULTS);applyCSS(DEFAULTS);});
async function initSettings(){
  try{
    const r=await fetch('/settings');
    const s=await r.json();
    CFG=Object.assign({},DEFAULTS,s);
    applyCSS(CFG);fillForm(CFG);maxShow=+(CFG.count||5);
  }catch(e){CFG=Object.assign({},DEFAULTS);applyCSS(CFG);fillForm(CFG);}
}
initSettings();
let maxShow=5;
const box=document.getElementById('messages');
const liveEls=new Map();
function msgKey(m){return m.id!==undefined?String(m.id):m.author+'\0'+m.original;}
function mkEl(m){
  const a=esc(m.author),o=esc(m.original);
  const d=document.createElement('div');
  // 通知メッセージ
  if(m.isNotice){
    if(!CFG.twShowNotices)return null;
    d.className='msg notice';
    d.innerHTML=`<div class="meta"><span class="badge-vip">${esc(m.noticeType)}</span><span class="author">${a}</span></div><div class="translated-text">${esc(m.noticeMsg)}</div>${m.original?'<div class="original">'+o+'</div>':''}`;
    return d;
  }
  d.className=m.translated?'msg translated':'msg japanese';
  // ユーザー名色: Twitch色が有効 && 色情報あり → Twitch色、なければ共通色
  const nameColor=(CFG.twUseTwitchColor && m.twitchColor)?m.twitchColor:'';
  const nameStyle=nameColor?` style="color:${esc(nameColor)}"`:'';
  if(m.translated){
    d.innerHTML=`<div class="meta">${avatar(m)}${badges(m)}<span class="author"${nameStyle}>${a}</span><span class="lang-badge">${langName(m.lang)}</span></div><div class="translated-text">${esc(m.translated)}</div><div class="original">${o}</div>`;
  }else{
    d.innerHTML=`<div class="meta">${avatar(m)}${badges(m)}<span class="author"${nameStyle}>${a}</span></div><div class="translated-text">${o}</div>`;
  }
  return d;
}
async function poll(){
  try{
    const res=await fetch('/messages');
    const data=await res.json();
    const show=data.slice(0,maxShow);
    const newKeys=show.map(msgKey);
    const newSet=new Set(newKeys);
    for(const[k,el]of liveEls){if(!newSet.has(k)){el.remove();liveEls.delete(k);}}
    for(let i=0;i<show.length;i++){
      const k=newKeys[i];
      if(!liveEls.has(k)){
        const el=mkEl(show[i]);
        if(!el){continue;}
        liveEls.set(k,el);
      }
      const el=liveEls.get(k);
      const cur=box.children[i];
      if(cur!==el){box.insertBefore(el,cur||null);}
    }
  }catch(e){}
  setTimeout(poll,1000);
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function avatar(m){
  if(!CFG.ytShowAvatar)return '';
  return m.imageUrl?`<img class="avatar" src="${esc(m.imageUrl)}" onerror="this.style.display='none'">`:'';
}
function badges(m){
  if(m.badgeUrl)return`<img class="avatar" src="${esc(m.badgeUrl)}" title="バッジ" onerror="this.style.display='none'">`;
  let b='';
  if(m.isOwner)b+=`<span class="badge-owner">配信者</span>`;
  else if(m.isMod&&(CFG.ytShowMod!==false))b+=`<span class="badge-mod">モデ</span>`;
  if(m.isMember&&(CFG.ytShowMember!==false))b+=`<span class="badge-member">メンバー</span>`;
  if(m.isVip&&CFG.twShowVip)b+=`<span class="badge-vip">VIP</span>`;
  if(m.isFirstMsg&&CFG.twShowFirstMsg)b+=`<span class="badge-first">初</span>`;
  if(m.subMonths>0&&CFG.twShowSubMonths)b+=`<span class="badge-months">${m.subMonths}ヶ月</span>`;
  return b;
}
/* 言語名マップはサーバー(main.py の LANG_NAMES)から取得して一元管理 */
let LANG={};
fetch('/langs').then(r=>r.json()).then(d=>{LANG=d;}).catch(e=>{});
function langName(c){return LANG[String(c||'').toLowerCase()]||c;}
/* SHOWROOM hype */
const hypeBadge=document.getElementById('hype-badge');
const hypeNum=document.getElementById('hypeNum');
let hypeVisible=false;
function applyHypeVisibility(v){hypeVisible=(v==='on');hypeBadge.classList.toggle('visible',hypeVisible);}
async function pollHype(){
  if(!hypeVisible){setTimeout(pollHype,5000);return;}
  try{const r=await fetch('/showroom/hype');const d=await r.json();
    if(d.active&&d.online_user_num>0){hypeNum.textContent=d.online_user_num.toLocaleString();hypeBadge.classList.add('visible');}
    else if(hypeVisible){hypeNum.textContent='--';}
  }catch(e){}
  setTimeout(pollHype,5000);
}
pollHype();
poll();
</script></body></html>"""


# ===== Flask ルート =====

@app.route('/')
def index():
    return render_template_string(OVERLAY_HTML, token=API_TOKEN)

@app.route('/langs')
def langs():
    """言語コード→日本語名マップ（オーバーレイ/GUIが参照。一元管理）"""
    return jsonify(LANG_NAMES)

@app.route('/settings', methods=['GET', 'POST'])
def overlay_settings():
    global _overlay_settings
    if flask_request.method == 'POST':
        data = flask_request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"status": "error", "message": "JSON body required"}), 400
        _overlay_settings.update(data)
        _save_settings()
        return jsonify({"status": "ok"})
    return jsonify(_overlay_settings)

@app.route('/messages')
def get_messages():
    with messages_lock:
        return jsonify(list(chat_messages))

@app.route('/start/<path:video_id>')
def start_chat(video_id):
    global chat_thread, _last_video_id
    stop_event.clear()
    # キューをフラッシュ
    while not translation_q.empty():
        try:
            translation_q.get_nowait()
            translation_q.task_done()
        except:
            break
    # ワーカーが死んでいたら再起動
    if not worker_threads or not any(t.is_alive() for t in worker_threads):
        start_workers()
    platform, identifier = _detect_platform(video_id)
    # DEMOモード: 実チャット接続をスキップ
    if platform == "demo":
        _last_video_id = None  # watchdog 対象外
        with messages_lock:
            chat_messages.clear()
        return jsonify({"status": "started", "platform": "demo", "video_id": video_id})
    _last_video_id = video_id  # watchdog 再起動用
    if chat_thread and chat_thread.is_alive():
        return jsonify({"status": "already running", "video_id": video_id})
    chat_thread = threading.Thread(target=chat_worker, args=(video_id,), daemon=True)
    chat_thread.start()
    return jsonify({"status": "started", "platform": platform, "id": identifier})

@app.route('/stop')
def stop_chat():
    global _last_video_id
    _last_video_id = None  # watchdog による再起動を止める
    stop_event.set()
    with messages_lock:
        chat_messages.clear()
    return jsonify({"status": "stopped"})

@app.route('/status')
def status():
    running = chat_thread is not None and chat_thread.is_alive()
    with messages_lock:
        count = len(chat_messages)
    return jsonify({
        "running": running,
        "message_count": count,
        "queue_size": translation_q.qsize(),
        "workers": NUM_WORKERS,
        "engine": TRANSLATE_ENGINE,
    })

@app.route('/test_batch', methods=['POST'])
def test_batch():
    """デモモード用: GUIからのバッチ注入"""
    data = flask_request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify({"error": "list required"}), 400
    with messages_lock:
        chat_messages.clear()
        for m in reversed(data):
            if isinstance(m, dict) and "id" not in m:
                m = {**m, "id": next(_MSG_COUNTER)}
            chat_messages.insert(0, m)
    return jsonify({"status": "ok", "count": len(data)})

@app.route('/test')
def test_inject():
    """ダミーメッセージ注入（UIテスト用）"""
    samples = [
        {"author": "Alice",   "original": "Hello! Great stream!", "translated": "こんにちは！素晴らしい配信！", "lang": "en"},
        {"author": "박민준",   "original": "안녕하세요 좋은 방송이에요", "translated": "こんにちは、良い放送ですね", "lang": "ko"},
        {"author": "Иван",    "original": "Отличный стрим!", "translated": "素晴らしいストリームです！", "lang": "ru"},
        {"author": "田中太郎", "original": "よろしくお願いします！", "translated": None, "lang": "ja"},
        {"author": "Wang Lei","original": "你好！很棒的直播", "translated": "こんにちは！素晴らしいライブ配信", "lang": "zh-cn"},
    ]
    with messages_lock:
        chat_messages.clear()
        for s in reversed(samples):
            chat_messages.insert(0, {**s, "id": next(_MSG_COUNTER)})
    return jsonify({"status": "ok", "injected": len(samples)})

@app.route('/showroom/start/<int:room_id>')
def showroom_start(room_id):
    ok = start_showroom(room_id)
    return jsonify({"status": "started" if ok else "already running", "room_id": room_id})

@app.route('/showroom/stop')
def showroom_stop_route():
    stop_showroom()
    return jsonify({"status": "stopped"})

@app.route('/showroom/hype')
def showroom_hype():
    with _showroom_lock:
        return jsonify(dict(_showroom_data))

GRAPRO_HEALTH_URL = "https://lt.f1234k.com/relay/health"

@app.route('/api_health')
def api_health():
    """軽量ヘルスチェック（実翻訳しない＝翻訳API課金なし）。
    GUI の定期ポーリングはこちらを使う。実翻訳の /lt_check は起動時・エンジン切替時のみ。"""
    try:
        if TRANSLATE_ENGINE == "grapro":
            r = _TRANSLATE_SESSION.get(GRAPRO_HEALTH_URL, timeout=5)
            ok = r.ok
        elif TRANSLATE_ENGINE == "libretranslate":
            base = LIBRETRANSLATE_URL.rsplit("/", 1)[0]
            r = _TRANSLATE_SESSION.get(f"{base}/languages", timeout=5)
            ok = r.ok
        else:  # deepl: キー未設定なら明確にエラー扱い
            ok = bool(DEEPL_API_KEY)
        return jsonify({"status": "ok" if ok else "error", "engine": TRANSLATE_ENGINE})
    except Exception as e:
        return jsonify({"status": "error", "engine": TRANSLATE_ENGINE, "error": str(e)})

@app.route('/lt_check')
def lt_check():
    """翻訳エンジン疎通確認（エンジン問わず "Hello world" を翻訳してみる）"""
    test_text = "Hello world"
    translated_text, detected_lang = translate_text(test_text, "en")
    if _last_translate_error:
        return jsonify({"status": "error", "engine": TRANSLATE_ENGINE,
                        "error": _last_translate_error})
    if translated_text == test_text:
        return jsonify({"status": "warning", "engine": TRANSLATE_ENGINE,
                        "result": translated_text, "detected_lang": detected_lang,
                        "note": "翻訳結果が原文と同一"})
    return jsonify({"status": "ok", "engine": TRANSLATE_ENGINE,
                    "result": translated_text, "detected_lang": detected_lang})

@app.route('/server_notification')
def server_notification():
    """サーバーからの通知を取得（GUIポーリング用）。取得後クリア。"""
    global _server_notification
    notif = _server_notification
    _server_notification = None
    if notif:
        return jsonify(notif)
    return jsonify({"type": None})

@app.route('/bouyomi', methods=['GET', 'POST'])
def bouyomi():
    """棒読みちゃん連携の状態取得・設定変更"""
    global _bouyomi_enabled, _bouyomi_port
    if flask_request.method == 'GET':
        return jsonify({"enabled": _bouyomi_enabled, "port": _bouyomi_port})
    data = flask_request.get_json(silent=True) or {}
    if "enabled" in data:
        _bouyomi_enabled = bool(data["enabled"])
    if "port" in data:
        _bouyomi_port = int(data["port"])
    return jsonify({"enabled": _bouyomi_enabled, "port": _bouyomi_port})

@app.route('/lt_url', methods=['GET', 'POST'])
def lt_url():
    """翻訳エンジン設定の取得・変更"""
    global LIBRETRANSLATE_URL, TRANSLATE_ENGINE
    if flask_request.method == 'GET':
        return jsonify({"url": LIBRETRANSLATE_URL, "engine": TRANSLATE_ENGINE})
    data = flask_request.get_json(silent=True) or {}
    # エンジン切替
    new_engine = data.get("engine", "").strip()
    if new_engine and new_engine in _ENGINES:
        TRANSLATE_ENGINE = new_engine
        print(f"[設定変更] 翻訳エンジン → {new_engine}")
    # LibreTranslate URL変更
    new_url = data.get("url", "").strip()
    if new_url:
        LIBRETRANSLATE_URL = new_url
        if not new_engine:
            TRANSLATE_ENGINE = "libretranslate"
        print(f"[設定変更] LibreTranslate URL → {new_url}")
    if new_engine or new_url:
        return jsonify({"status": "ok", "url": LIBRETRANSLATE_URL, "engine": TRANSLATE_ENGINE})
    return jsonify({"status": "error", "message": "engine or url required"}), 400


# ===== クライアントID =====

def _get_client_id():
    """config.json から worker_id を取得。なければ新規発行して保存。
    ※ __file__ 基準だと PyInstaller onefile で一時フォルダに書いてしまい
      起動ごとに ID が変わる（レート制限・ブロックが無効化される）ため BASE_DIR 基準。"""
    cfg_path = os.path.join(BASE_DIR, "config.json")
    cfg = {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _json.load(f)
    except: pass
    wid = cfg.get("worker_id")
    if not wid:
        wid = str(_uuid.uuid4())
        cfg["worker_id"] = wid
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
        except: pass
        print(f"[クライアント] 新規ID発行: {wid}")
    return wid

CLIENT_ID = _get_client_id()

@app.route('/client_id')
def client_id():
    """クライアントIDを返す"""
    return jsonify({"worker_id": CLIENT_ID, "version": VERSION})


# ===== エントリーポイント =====
if __name__ == '__main__':
    print("=" * 50)
    print(f"GRAPRO-TRANSLATOR v{VERSION}")
    print(f"翻訳エンジン  : {TRANSLATE_ENGINE}")
    print(f"オーバーレイ  : http://localhost:{OVERLAY_PORT}/")
    print(f"チャット開始  : http://localhost:{OVERLAY_PORT}/start/<video_id>?token=<TOKEN>")
    print(f"停止          : http://localhost:{OVERLAY_PORT}/stop?token=<TOKEN>")
    print(f"ステータス    : http://localhost:{OVERLAY_PORT}/status")
    print(f"UIテスト      : http://localhost:{OVERLAY_PORT}/test")
    print(f"疎通確認      : http://localhost:{OVERLAY_PORT}/lt_check?token=<TOKEN>")
    print(f"APIトークン   : {API_TOKEN}")
    print("=" * 50)

    # ワーカー起動
    start_workers()

    # 引数で video_id を受け取った場合は自動開始
    if len(sys.argv) > 1:
        vid = sys.argv[1]
        print(f"[自動開始] video_id={vid}")
        chat_thread = threading.Thread(target=chat_worker, args=(vid,), daemon=True)
        chat_thread.start()

    app.run(host='127.0.0.1', port=OVERLAY_PORT, debug=False, use_reloader=False)
