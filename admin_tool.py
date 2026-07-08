"""
GRAPRO 管理ツール — relay_server の admin API を操作
使い方:
  python admin_tool.py stats              — 利用統計を表示
  python admin_tool.py warn  <ID> [メッセージ]  — クライアントに警告送信
  python admin_tool.py block <ID>         — クライアントをブロック
  python admin_tool.py unblock <ID>       — ブロック解除

デフォルトではCloudflare Tunnel経由。--local で直接LXC接続。

■ 認証トークン（必須）
  admin/stats は Bearer トークンで保護されています。以下のいずれかで指定:
    - 環境変数           GRAPRO_ADMIN_TOKEN=xxxx
    - スクリプト隣のファイル admin_token.txt にトークンを1行で保存
  トークンはサーバー .env の GRAPRO_ADMIN_TOKEN と一致させること。
"""

import os
import sys
import json
import requests

# === エンドポイント設定 ===
RELAY_URL = "https://lt.f1234k.com/relay"

def _url(path):
    return f"{RELAY_URL}{path}"

def _load_token():
    """環境変数 or admin_token.txt から管理トークンを取得"""
    tok = os.environ.get("GRAPRO_ADMIN_TOKEN", "").strip()
    if tok:
        return tok
    tok_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_token.txt")
    try:
        with open(tok_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""

def _headers():
    tok = _load_token()
    if not tok:
        print("⚠ 管理トークンが未設定です。GRAPRO_ADMIN_TOKEN 環境変数か admin_token.txt を設定してください。")
        sys.exit(2)
    return {"Authorization": f"Bearer {tok}"}

def cmd_stats():
    """利用統計を表示"""
    r = requests.get(_url("/stats"), headers=_headers(), timeout=10)
    d = r.json()
    print(f"アクティブクライアント: {d['active_clients']}")
    print(f"直近1分リクエスト数:   {d['requests_last_min']}")
    print(f"ブロック数:            {d['blocked_count']}")
    if d.get("client_detail"):
        print("\nクライアント別:")
        for cid, count in d["client_detail"].items():
            short = cid[:8] + "..." if len(cid) > 12 else cid
            print(f"  {short}  {count} req/min")

def cmd_warn(worker_id, message=None):
    """クライアントに警告送信"""
    payload = {"worker_id": worker_id}
    if message:
        payload["message"] = message
    r = requests.post(_url("/admin/warn"), headers=_headers(), json=payload, timeout=10)
    d = r.json()
    if r.ok:
        print(f"警告セット完了: {d.get('worker_id', '')}")
        print(f"メッセージ: {d.get('message', '')}")
    else:
        print(f"エラー: {d}")

def cmd_block(worker_id):
    """クライアントをブロック"""
    r = requests.post(_url("/admin/block"), headers=_headers(), json={"worker_id": worker_id}, timeout=10)
    d = r.json()
    if r.ok:
        print(f"ブロック完了: {d.get('worker_id', '')}")
    else:
        print(f"エラー: {d}")

def cmd_unblock(worker_id):
    """ブロック解除"""
    r = requests.post(_url("/admin/unblock"), headers=_headers(), json={"worker_id": worker_id}, timeout=10)
    d = r.json()
    if r.ok:
        print(f"ブロック解除: {d.get('worker_id', '')}")
    else:
        print(f"エラー: {d}")

def cmd_health():
    """ヘルスチェック"""
    r = requests.get(_url("/health"), timeout=10)
    d = r.json()
    print(f"ステータス: {d.get('status', '?')}  エンジン: {d.get('engine', '?')}")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "stats":
        cmd_stats()
    elif cmd == "health":
        cmd_health()
    elif cmd == "warn":
        if len(sys.argv) < 3:
            print("使い方: admin_tool.py warn <worker_id> [メッセージ]")
            sys.exit(1)
        wid = sys.argv[2]
        msg = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else None
        cmd_warn(wid, msg)
    elif cmd == "block":
        if len(sys.argv) < 3:
            print("使い方: admin_tool.py block <worker_id>")
            sys.exit(1)
        cmd_block(sys.argv[2])
    elif cmd == "unblock":
        if len(sys.argv) < 3:
            print("使い方: admin_tool.py unblock <worker_id>")
            sys.exit(1)
        cmd_unblock(sys.argv[2])
    else:
        print(f"不明なコマンド: {cmd}")
        print(__doc__)
        sys.exit(1)

if __name__ == "__main__":
    main()
