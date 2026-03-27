"""
OBS YouTube Live Chat 翻訳ツール
- pytchat でYouTubeライブチャットを取得
- langdetect で言語判定（オフライン）
- LibreTranslate で日本語以外を翻訳（キュー処理・複数ワーカー対応）
- Flask でOBS ブラウザソース用オーバーレイを提供

使い方:
  python main.py                  # サーバーのみ起動
  python main.py <video_id>       # 起動時にチャット開始
  GET /start/<video_id>           # チャット開始
  GET /stop                       # チャット停止
  GET /status                     # 状態確認
  GET /test                       # ダミーデータ注入（UIテスト用）
  GET /lt_check                   # LibreTranslate 疎通確認
"""

import threading
import time
import queue
import sys
import os
import json as _json
from flask import Flask, render_template_string, jsonify, request as flask_request
import requests
from langdetect import detect, LangDetectException

# ===== 設定 =====
OVERLAY_PORT       = 7788
MAX_MESSAGES       = 20    # オーバーレイ表示の最大件数
MIN_CHARS          = 3     # 翻訳する最小文字数
TARGET_LANG        = "ja"  # 翻訳先言語
NUM_WORKERS        = 3     # 翻訳ワーカースレッド数

# ===== 翻訳エンジン設定 =====
# "libretranslate" | "deepl" | "azure"   ← 今後 "google" 等も追加可能
TRANSLATE_ENGINE = os.environ.get("TRANSLATE_ENGINE", "deepl")

# --- LibreTranslate ---
# 公開IP経由 (MAP-E固定IP → Proxmox socat → LXC LibreTranslate)
LIBRETRANSLATE_URL     = "https://lt.f1234k.com/translate"
LIBRETRANSLATE_API_KEY = "47fcc4e7-6a4b-43e3-967b-c60c5438f8d3"

# --- DeepL ---
DEEPL_API_KEY = os.environ.get("DEEPL_API_KEY", "REDACTED_DEEPL_KEY")
# Free版: api-free.deepl.com / Pro版: api.deepl.com
DEEPL_API_URL = "https://api-free.deepl.com/v2/translate"

# --- Azure Translator ---
AZURE_API_KEY    = os.environ.get("AZURE_TRANSLATOR_KEY", "REDACTED_AZURE_KEY")
AZURE_REGION     = os.environ.get("AZURE_TRANSLATOR_REGION", "japaneast")
AZURE_API_URL    = "https://api.cognitive.microsofttranslator.com/translate"

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
translation_q   = queue.Queue()   # 翻訳キュー
stop_event      = threading.Event()
chat_thread     = None
worker_threads  = []

app = Flask(__name__)

# ===== オーバーレイ設定（サーバー側永続化） =====
_SETTINGS_FILE = "overlay_settings.json"
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
}
_overlay_settings = dict(_DEFAULT_SETTINGS)

def _load_settings():
    global _overlay_settings
    try:
        import os
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
    """LibreTranslate で翻訳"""
    resp = requests.post(LIBRETRANSLATE_URL, json={
        "q": text, "source": source_lang, "target": TARGET_LANG, "format": "text",
        "api_key": LIBRETRANSLATE_API_KEY,
    }, timeout=5)
    if resp.status_code == 200:
        return resp.json().get("translatedText", text)
    return text


def _translate_deepl(text, source_lang):
    """DeepL API で翻訳"""
    src = _DEEPL_SOURCE_MAP.get(source_lang, source_lang.upper())
    tgt = _DEEPL_TARGET_MAP.get(TARGET_LANG, TARGET_LANG.upper())
    resp = requests.post(DEEPL_API_URL, data={
        "auth_key": DEEPL_API_KEY,
        "text": text,
        "source_lang": src,
        "target_lang": tgt,
    }, timeout=5)
    if resp.status_code == 200:
        translations = resp.json().get("translations", [])
        if translations:
            return translations[0].get("text", text)
    else:
        print(f"[DeepL] HTTP {resp.status_code}: {resp.text[:200]}")
    return text


def _translate_azure(text, source_lang):
    """Azure Translator API で翻訳"""
    import uuid
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_API_KEY,
        "Ocp-Apim-Subscription-Region": AZURE_REGION,
        "Content-Type": "application/json",
        "X-ClientTraceId": str(uuid.uuid4()),
    }
    params = {"api-version": "3.0", "from": source_lang, "to": TARGET_LANG}
    body = [{"text": text}]
    resp = requests.post(AZURE_API_URL, headers=headers, params=params,
                         json=body, timeout=5)
    if resp.status_code == 200:
        result = resp.json()
        if result and result[0].get("translations"):
            return result[0]["translations"][0].get("text", text)
    else:
        print(f"[Azure] HTTP {resp.status_code}: {resp.text[:200]}")
    return text


# エンジン名 → 翻訳関数のマッピング
_ENGINES = {
    "libretranslate": _translate_libretranslate,
    "deepl":          _translate_deepl,
    "azure":          _translate_azure,
}


def translate_text(text, source_lang):
    """設定されたエンジンで翻訳。失敗時は原文を返す"""
    engine_fn = _ENGINES.get(TRANSLATE_ENGINE, _translate_libretranslate)
    try:
        return engine_fn(text, source_lang)
    except Exception as e:
        print(f"[翻訳エラー][{TRANSLATE_ENGINE}] {e}")
    return text


import re as _re
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
    """オフライン言語判定。絵文字のみ・短すぎる・失敗時は None"""
    stripped = text.strip()
    if len(stripped) < MIN_CHARS:
        return None
    # 絵文字・記号のみのコメントは翻訳不要（日本語扱いにして表示だけする）
    if _EMOJI_ONLY.match(stripped):
        return TARGET_LANG
    try:
        return detect(text)
    except LangDetectException:
        return None


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

        author   = item["author"]
        message  = item["message"]
        lang     = item["lang"]
        imageUrl = item.get("imageUrl", "")
        badgeUrl = item.get("badgeUrl", "")
        isMember = item.get("isMember", False)
        isMod    = item.get("isMod", False)
        isOwner  = item.get("isOwner", False)

        if lang == TARGET_LANG:
            entry = {"author": author, "original": message, "translated": None,
                     "lang": lang, "imageUrl": imageUrl, "badgeUrl": badgeUrl,
                     "isMember": isMember, "isMod": isMod, "isOwner": isOwner}
        else:
            translated = translate_text(message, lang)
            entry = {"author": author, "original": message, "translated": translated,
                     "lang": lang, "imageUrl": imageUrl, "badgeUrl": badgeUrl,
                     "isMember": isMember, "isMod": isMod, "isOwner": isOwner}

        with messages_lock:
            chat_messages.insert(0, entry)
            if len(chat_messages) > MAX_MESSAGES:
                chat_messages.pop()

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


def chat_worker(video_id):
    """YouTube Live Chat API で直接チャット取得 → 言語判定 → 翻訳キューへ投入"""
    print(f"[チャット開始] video_id={video_id}")
    retry = 0
    while not stop_event.is_set() and retry < 5:
        try:
            api_key, continuation = _get_initial_chat_info(video_id)
            if not continuation:
                print(f"[チャット] continuationトークン取得失敗 retry={retry+1}")
                retry += 1
                time.sleep(3)
                continue
            print(f"[チャット] 接続成功 retry={retry}")
            retry = 0
            while continuation and not stop_event.is_set():
                msgs, next_cont, wait_ms = _poll_live_chat(api_key, continuation)
                for m in msgs:
                    lang = detect_language(m["message"])
                    if lang is None:
                        continue
                    m["lang"] = lang
                    translation_q.put(m)
                if next_cont:
                    continuation = next_cont
                else:
                    print("[チャット] continuation終了（配信終了?）")
                    break
                # YouTube指定の待機時間（最低0.5秒、最大10秒に制限）
                sleep_sec = max(0.5, min(wait_ms / 1000, 10))
                # stop_eventを細かくチェックしながら待機
                waited = 0
                while waited < sleep_sec and not stop_event.is_set():
                    time.sleep(0.3)
                    waited += 0.3
        except Exception as e:
            print(f"[チャットエラー] {e}")
            if not stop_event.is_set():
                retry += 1
                time.sleep(3)
    if retry >= 5:
        print("[チャット] 再接続5回失敗、終了")
    print("[チャット終了]")


def start_workers():
    """翻訳ワーカースレッドを NUM_WORKERS 本起動"""
    global worker_threads
    worker_threads = []
    for i in range(NUM_WORKERS):
        t = threading.Thread(target=translation_worker, daemon=True, name=f"worker-{i}")
        t.start()
        worker_threads.append(t)
    print(f"[ワーカー起動] {NUM_WORKERS} 本")


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
  .msg{background:var(--msg-bg);border-radius:8px;padding:8px 14px;color:var(--text-color);font-family:var(--text-font);font-size:var(--text-size);line-height:1.6;animation:fadein 0.35s ease;word-break:break-word;text-shadow:0 1px 3px rgba(0,0,0,0.8)}
  .msg.translated{border-left:4px solid var(--accent-translated)}
  .msg.japanese{border-left:4px solid var(--accent-japanese)}
  .meta{display:flex;align-items:center;gap:6px;margin-bottom:4px}
  .avatar{width:22px;height:22px;border-radius:50%;object-fit:cover;flex-shrink:0;border:1px solid rgba(255,255,255,0.3)}
  .author{color:var(--author-color);font-family:var(--author-font);font-weight:bold;font-size:var(--author-size)}
  .lang-badge{font-size:11px;background:var(--accent-translated);color:#003;border-radius:4px;padding:1px 7px;font-weight:bold;text-shadow:none !important;display:inline-block}
  .badge-member{font-size:10px;background:#2ecc71;color:#000;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-mod{font-size:10px;background:#5865f2;color:#fff;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-owner{font-size:10px;background:#f1c40f;color:#000;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .translated-text{color:var(--text-color);font-family:var(--text-font);font-size:var(--text-size)}
  .original{color:var(--original-color);font-size:13px;margin-top:3px}
  @keyframes fadein{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
  #gear{position:fixed;top:8px;right:8px;width:32px;height:32px;background:rgba(60,60,60,0.7);border:none;border-radius:50%;color:#ccc;font-size:18px;cursor:pointer;z-index:1000;display:flex;align-items:center;justify-content:center;transition:background 0.2s,transform 0.3s}
  #gear:hover{background:rgba(100,100,100,0.9);transform:rotate(45deg)}
  #panel{display:none;position:fixed;top:0;right:0;width:320px;height:100vh;background:rgba(20,20,25,0.96);z-index:999;overflow-y:auto;padding:16px;color:#ddd;font-family:'Meiryo','Noto Sans JP',sans-serif;font-size:13px;border-left:1px solid #333}
  #panel.open{display:block}
  #panel h3{color:#fff;font-size:15px;margin:0 0 12px;border-bottom:1px solid #444;padding-bottom:6px}
  .sgroup{margin-bottom:14px}
  .sgroup label{display:block;color:#aaa;font-size:12px;margin-bottom:3px}
  .sgroup input[type=color]{width:40px;height:28px;border:1px solid #555;background:#222;cursor:pointer;vertical-align:middle}
  .sgroup input[type=range]{width:120px;vertical-align:middle}
  .sgroup select{background:#222;color:#ddd;border:1px solid #555;padding:3px 6px;font-size:12px}
  .sgroup .val{color:#888;font-size:11px;margin-left:4px}
  .srow{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  #panel .btn-row{display:flex;gap:8px;margin-top:12px}
  #panel button{background:#333;color:#ccc;border:1px solid #555;border-radius:4px;padding:5px 14px;font-size:12px;cursor:pointer}
  #panel button:hover{background:#444}
  #panel button.primary{background:#29b6f6;color:#000;border-color:#29b6f6}
  #panel button.primary:hover{background:#4fc3f7}
</style></head><body>
<button id="gear" title="設定">&#9881;</button>
<div id="panel">
  <h3>&#9881; オーバーレイ設定</h3>
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
  <div class="btn-row">
    <button class="primary" id="btnSave">保存</button>
    <button id="btnReset">リセット</button>
    <button id="btnClose">閉じる</button>
  </div>
</div>
<div id="messages"></div>
<script>
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
}
function toHex(c){
  if(/^#[0-9a-f]{6}$/i.test(c))return c;
  if(/^#[0-9a-f]{3}$/i.test(c))return '#'+c[1]+c[1]+c[2]+c[2]+c[3]+c[3];
  const d=document.createElement('div');d.style.color=c;document.body.appendChild(d);
  const m=getComputedStyle(d).color.match(/\d+/g);d.remove();
  if(!m)return'#000000';
  return'#'+m.slice(0,3).map(x=>(+x).toString(16).padStart(2,'0')).join('');
}
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
  };
}
function liveUpdate(){applyCSS(readForm());}
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
  try{
    await fetch('/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(s)});
    document.getElementById('btnSave').textContent='\u4fdd\u5b58\u3057\u307e\u3057\u305f!';
    setTimeout(()=>document.getElementById('btnSave').textContent='\u4fdd\u5b58',1500);
  }catch(e){alert('\u4fdd\u5b58\u5931\u6557: '+e);}
});
const DEFAULTS={bodyBg:'transparent',msgBg:'rgba(0,0,0,0.88)',accentTranslated:'#29b6f6',accentJapanese:'#555555',authorColor:'#ffe066',authorFont:'Meiryo, Noto Sans JP, sans-serif',authorSize:'14',textColor:'#ffffff',textFont:'Meiryo, Noto Sans JP, sans-serif',textSize:'18',originalColor:'#bbbbbb',count:'5'};
document.getElementById('btnReset').addEventListener('click',()=>{fillForm(DEFAULTS);applyCSS(DEFAULTS);});
async function initSettings(){
  try{
    const r=await fetch('/settings');
    const s=await r.json();
    applyCSS(s);fillForm(s);maxShow=+(s.count||5);
  }catch(e){applyCSS(DEFAULTS);fillForm(DEFAULTS);}
}
initSettings();
let maxShow=5;
const box=document.getElementById('messages');
const liveEls=new Map();
function msgKey(m){return m.author+'\0'+m.original;}
function mkEl(m){
  const a=esc(m.author),o=esc(m.original);
  const d=document.createElement('div');
  d.className=m.translated?'msg translated':'msg japanese';
  if(m.translated){
    d.innerHTML=`<div class="meta">${avatar(m)}${badges(m)}<span class="author">${a}</span><span class="lang-badge">${langName(m.lang)}</span></div><div class="translated-text">${esc(m.translated)}</div><div class="original">${o}</div>`;
  }else{
    d.innerHTML=`<div class="meta">${avatar(m)}${badges(m)}<span class="author">${a}</span></div><div class="translated-text">${o}</div>`;
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
        liveEls.set(k,el);
      }
      const el=liveEls.get(k);
      const cur=box.children[i];
      if(cur!==el){box.insertBefore(el,cur||null);}
    }
  }catch(e){}
  setTimeout(poll,1000);
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function avatar(m){return m.imageUrl?`<img class="avatar" src="${esc(m.imageUrl)}" onerror="this.style.display='none'">`:''}
function badges(m){
  if(m.badgeUrl)return`<img class="avatar" src="${esc(m.badgeUrl)}" title="バッジ" onerror="this.style.display='none'">`;
  let b='';
  if(m.isOwner)b+=`<span class="badge-owner">配信者</span>`;
  else if(m.isMod)b+=`<span class="badge-mod">モデ</span>`;
  if(m.isMember)b+=`<span class="badge-member">メンバー</span>`;
  return b;
}
const LANG={af:'アフリカーンス語',ar:'アラビア語',az:'アゼルバイジャン語',bg:'ブルガリア語',bn:'ベンガル語',ca:'カタルーニャ語',cs:'チェコ語',da:'デンマーク語',de:'ドイツ語',el:'ギリシャ語',en:'英語',eo:'エスペラント語',es:'スペイン語',et:'エストニア語',fa:'ペルシャ語',fi:'フィンランド語',fr:'フランス語',he:'ヘブライ語',hi:'ヒンディー語',hu:'ハンガリー語',id:'インドネシア語',it:'イタリア語',ko:'韓国語',lt:'リトアニア語',lv:'ラトビア語',ms:'マレー語',nl:'オランダ語',pl:'ポーランド語',pt:'ポルトガル語','pt-br':'ポルトガル語(BR)',ro:'ルーマニア語',ru:'ロシア語',sk:'スロバキア語',sl:'スロベニア語',sq:'アルバニア語',sv:'スウェーデン語',th:'タイ語',tl:'タガログ語',tr:'トルコ語',uk:'ウクライナ語',ur:'ウルドゥー語',vi:'ベトナム語',zh:'中国語','zh-cn':'中国語','zh-hans':'中国語(簡体)','zh-tw':'中国語(繁体)','zh-hant':'中国語(繁体)'};
function langName(c){return LANG[c.toLowerCase()]||c;}
poll();
</script></body></html>"""


# ===== Flask ルート =====

@app.route('/')
def index():
    return render_template_string(OVERLAY_HTML)

@app.route('/settings', methods=['GET', 'POST'])
def overlay_settings():
    global _overlay_settings
    if flask_request.method == 'POST':
        data = flask_request.get_json(force=True)
        _overlay_settings.update(data)
        _save_settings()
        return jsonify({"status": "ok"})
    return jsonify(_overlay_settings)

@app.route('/messages')
def get_messages():
    with messages_lock:
        return jsonify(list(chat_messages))

@app.route('/start/<video_id>')
def start_chat(video_id):
    global chat_thread
    stop_event.clear()
    # キューをフラッシュ
    while not translation_q.empty():
        try: translation_q.get_nowait()
        except: break
    if chat_thread and chat_thread.is_alive():
        return jsonify({"status": "already running", "video_id": video_id})
    chat_thread = threading.Thread(target=chat_worker, args=(video_id,), daemon=True)
    chat_thread.start()
    return jsonify({"status": "started", "video_id": video_id})

@app.route('/stop')
def stop_chat():
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
            chat_messages.insert(0, s)
    return jsonify({"status": "ok", "injected": len(samples)})

@app.route('/lt_check')
def lt_check():
    """翻訳エンジン疎通確認（エンジン問わず "Hello world" を翻訳してみる）"""
    try:
        result = translate_text("Hello world", "en")
        return jsonify({"status": "ok", "engine": TRANSLATE_ENGINE, "result": result})
    except Exception as e:
        return jsonify({"status": "unreachable", "engine": TRANSLATE_ENGINE, "error": str(e)})


# ===== エントリーポイント =====
if __name__ == '__main__':
    print("=" * 50)
    print("OBS YouTube 翻訳ツール")
    print(f"翻訳エンジン  : {TRANSLATE_ENGINE}")
    print(f"オーバーレイ  : http://localhost:{OVERLAY_PORT}/")
    print(f"チャット開始  : http://localhost:{OVERLAY_PORT}/start/<video_id>")
    print(f"停止          : http://localhost:{OVERLAY_PORT}/stop")
    print(f"ステータス    : http://localhost:{OVERLAY_PORT}/status")
    print(f"UIテスト      : http://localhost:{OVERLAY_PORT}/test")
    print(f"疎通確認      : http://localhost:{OVERLAY_PORT}/lt_check")
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
