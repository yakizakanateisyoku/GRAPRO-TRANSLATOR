"""
OBS YouTube Live Chat 翻訳ツール
- pytchat でYouTubeライブチャットを取得
- langdetect で言語判定（オフライン）
- LibreTranslate で日本語以外を翻訳
- Flask でOBS ブラウザソース用オーバーレイを提供

使い方:
  python main.py                  # サーバーのみ起動
  python main.py <video_id>       # 起動時にチャット開始
  GET /start/<video_id>           # チャット開始
  GET /stop                       # チャット停止
  GET /status                     # 状態確認
  GET /test                       # ダミーメッセージ注入（UIテスト用）
  GET /lt_check                   # LibreTranslate 疎通確認
"""

import threading
import time
import sys
from flask import Flask, render_template_string, jsonify
import pytchat
import requests
from langdetect import detect, LangDetectException

# ===== 設定 =====
LIBRETRANSLATE_URL = "http://192.168.1.15:5000/translate"
OVERLAY_PORT = 7788
MAX_MESSAGES = 20
MIN_CHARS = 3
TARGET_LANG = "ja"

# ===== グローバル状態 =====
chat_messages = []
messages_lock = threading.Lock()
stop_event = threading.Event()
chat_thread = None

app = Flask(__name__)


# ===== 翻訳・言語判定 =====

def translate_text(text, source_lang):
    try:
        resp = requests.post(LIBRETRANSLATE_URL, json={
            "q": text, "source": source_lang, "target": TARGET_LANG, "format": "text"
        }, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("translatedText", text)
    except Exception as e:
        print(f"[翻訳エラー] {e}")
    return text


def detect_language(text):
    if len(text.strip()) < MIN_CHARS:
        return None
    try:
        return detect(text)
    except LangDetectException:
        return None


# ===== チャット処理 =====

def chat_worker(video_id):
    print(f"[チャット開始] video_id={video_id}")
    try:
        chat = pytchat.create(video_id=video_id)
        while chat.is_alive() and not stop_event.is_set():
            for item in chat.get().sync_items():
                process_message(item.author.name, item.message)
            time.sleep(0.5)
    except Exception as e:
        print(f"[チャットエラー] {e}")
    print("[チャット終了]")


def process_message(author, message):
    lang = detect_language(message)
    if lang is None:
        return
    if lang == TARGET_LANG:
        entry = {"author": author, "original": message, "translated": None, "lang": lang}
    else:
        entry = {"author": author, "original": message, "translated": translate_text(message, lang), "lang": lang}
    with messages_lock:
        chat_messages.insert(0, entry)
        if len(chat_messages) > MAX_MESSAGES:
            chat_messages.pop()


# ===== OBS オーバーレイ HTML =====
OVERLAY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:transparent;font-family:'Meiryo','Noto Sans JP',sans-serif;overflow:hidden}
  #messages{position:fixed;bottom:10px;left:10px;right:10px;display:flex;flex-direction:column-reverse;gap:4px;max-height:90vh;overflow:hidden}
  .msg{background:rgba(0,0,0,0.72);border-radius:6px;padding:5px 10px;color:#fff;font-size:14px;line-height:1.5;animation:fadein 0.4s ease;word-break:break-word}
  .msg.translated{border-left:3px solid #4af}
  .msg.japanese{border-left:3px solid #888}
  .author{color:#ffd700;font-weight:bold;font-size:12px}
  .lang-badge{display:inline-block;font-size:10px;background:#4af;color:#000;border-radius:3px;padding:0 4px;margin-left:4px;vertical-align:middle}
  .original{color:#aaa;font-size:12px;margin-top:1px}
  @keyframes fadein{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
</style></head><body>
<div id="messages"></div>
<script>
let lastCount=-1;
async function poll(){
  try{
    const res=await fetch('/messages');
    const data=await res.json();
    if(data.length!==lastCount){
      lastCount=data.length;
      document.getElementById('messages').innerHTML=data.map(m=>{
        const a=esc(m.author),o=esc(m.original);
        if(m.translated)return`<div class="msg translated"><span class="author">${a}</span><span class="lang-badge">${m.lang}</span><div>${esc(m.translated)}</div><div class="original">${o}</div></div>`;
        return`<div class="msg japanese"><span class="author">${a}</span><div>${o}</div></div>`;
      }).join('');
    }
  }catch(e){}
  setTimeout(poll,1000);
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
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
    return jsonify({"running": running, "message_count": count})

@app.route('/test')
def test_inject():
    """ダミーメッセージを注入してUIを確認"""
    samples = [
        {"author": "Alice", "original": "Hello! Great stream!", "translated": "こんにちは！素晴らしい配信！", "lang": "en"},
        {"author": "박민준", "original": "안녕하세요 좋은 방송이에요", "translated": "こんにちは、良い放送ですね", "lang": "ko"},
        {"author": "Иван", "original": "Отличный стрим!", "translated": "素晴らしいストリームです！", "lang": "ru"},
        {"author": "田中太郎", "original": "よろしくお願いします！", "translated": None, "lang": "ja"},
        {"author": "Wang Lei", "original": "你好！很棒的直播", "translated": "こんにちは！素晴らしいライブ配信", "lang": "zh-cn"},
    ]
    with messages_lock:
        chat_messages.clear()
        for s in reversed(samples):
            chat_messages.insert(0, s)
    return jsonify({"status": "ok", "injected": len(samples)})

@app.route('/lt_check')
def lt_check():
    """LibreTranslate の疎通確認"""
    try:
        resp = requests.post(LIBRETRANSLATE_URL, json={
            "q": "Hello world", "source": "en", "target": "ja", "format": "text"
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
    if len(sys.argv) > 1:
        vid = sys.argv[1]
        print(f"[自動開始] video_id={vid}")
        chat_thread = threading.Thread(target=chat_worker, args=(vid,), daemon=True)
        chat_thread.start()
    app.run(host='127.0.0.1', port=OVERLAY_PORT, debug=False, use_reloader=False)
