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
from flask import Flask, render_template_string, jsonify
import requests
from langdetect import detect, LangDetectException

# ===== 設定 =====
# 公開IP経由 (MAP-E固定IP → Proxmox socat → LXC LibreTranslate)
# 123.225.35.19:5000 → 192.168.1.11:5001 → 192.168.1.15:5000
LIBRETRANSLATE_URL = "http://123.225.35.19:5000/translate"
LIBRETRANSLATE_API_KEY = "c57f841d-53f3-4d83-a9ab-24288ed44413"  # 500 req/min
OVERLAY_PORT       = 7788
MAX_MESSAGES       = 20    # オーバーレイ表示の最大件数
MIN_CHARS          = 3     # 翻訳する最小文字数
TARGET_LANG        = "ja"  # 翻訳先言語
NUM_WORKERS        = 3     # 翻訳ワーカースレッド数

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


# ===== 翻訳・言語判定 =====

def translate_text(text, source_lang):
    """LibreTranslate で翻訳。失敗時は原文を返す"""
    try:
        resp = requests.post(LIBRETRANSLATE_URL, json={
            "q": text, "source": source_lang, "target": TARGET_LANG, "format": "text",
            "api_key": LIBRETRANSLATE_API_KEY,
        }, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("translatedText", text)
    except Exception as e:
        print(f"[翻訳エラー] {e}")
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
OVERLAY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:transparent;font-family:'Meiryo','Noto Sans JP',sans-serif;overflow:hidden}
  #messages{position:fixed;bottom:16px;left:16px;right:16px;display:flex;flex-direction:column-reverse;gap:6px;max-height:90vh;overflow:hidden}
  .msg{background:rgba(0,0,0,0.88);border-radius:8px;padding:8px 14px;color:#ffffff;font-size:18px;line-height:1.6;animation:fadein 0.35s ease;word-break:break-word;text-shadow:0 1px 3px rgba(0,0,0,0.8)}
  .msg.translated{border-left:4px solid #29b6f6}
  .msg.japanese{border-left:4px solid #555}
  .meta{display:flex;align-items:center;gap:6px;margin-bottom:4px}
  .avatar{width:22px;height:22px;border-radius:50%;object-fit:cover;flex-shrink:0;border:1px solid rgba(255,255,255,0.3)}
  .author{color:#ffe066;font-weight:bold;font-size:14px}
  .lang-badge{font-size:11px;background:#29b6f6;color:#003;border-radius:4px;padding:1px 7px;font-weight:bold;text-shadow:none !important;display:inline-block}
  .badge-member{font-size:10px;background:#2ecc71;color:#000;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-mod{font-size:10px;background:#5865f2;color:#fff;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .badge-owner{font-size:10px;background:#f1c40f;color:#000;border-radius:3px;padding:1px 5px;font-weight:bold;text-shadow:none}
  .translated-text{color:#ffffff;font-size:18px}
  .original{color:#bbb;font-size:13px;margin-top:3px}
  @keyframes fadein{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
</style></head><body>
<div id="messages"></div>
<script>
const maxShow=parseInt(new URLSearchParams(location.search).get('count')||'10',10);
let lastKey='';
async function poll(){
  try{
    const res=await fetch('/messages');
    const data=await res.json();
    const show=data.slice(0,maxShow);
    const key=show.map(m=>m.author+m.original).join('|');
    if(key!==lastKey){
      lastKey=key;
      document.getElementById('messages').innerHTML=show.map(m=>{
        const a=esc(m.author),o=esc(m.original);
        if(m.translated)return`<div class="msg translated"><div class="meta">${avatar(m)}${badges(m)}<span class="author">${a}</span><span class="lang-badge">${langName(m.lang)}</span></div><div class="translated-text">${esc(m.translated)}</div><div class="original">${o}</div></div>`;
        return`<div class="msg japanese"><div class="meta">${avatar(m)}${badges(m)}<span class="author">${a}</span></div><div class="translated-text">${o}</div></div>`;
      }).join('');
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
    """LibreTranslate 疎通確認"""
    try:
        resp = requests.post(LIBRETRANSLATE_URL, json={
            "q": "Hello world", "source": "en", "target": "ja", "format": "text",
            "api_key": LIBRETRANSLATE_API_KEY,
        }, timeout=5)
        if resp.status_code == 200:
            return jsonify({"status": "ok", "result": resp.json().get("translatedText", "")})
        return jsonify({"status": "error", "code": resp.status_code})
    except Exception as e:
        return jsonify({"status": "unreachable", "error": str(e)})


# ===== エントリーポイント =====
if __name__ == '__main__':
    print("=" * 50)
    print("OBS YouTube 翻訳ツール")
    print(f"オーバーレイ  : http://localhost:{OVERLAY_PORT}/")
    print(f"チャット開始  : http://localhost:{OVERLAY_PORT}/start/<video_id>")
    print(f"停止          : http://localhost:{OVERLAY_PORT}/stop")
    print(f"ステータス    : http://localhost:{OVERLAY_PORT}/status")
    print(f"UIテスト      : http://localhost:{OVERLAY_PORT}/test")
    print(f"LT疎通確認    : http://localhost:{OVERLAY_PORT}/lt_check")
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
