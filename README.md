# OBS YouTube Live Chat 翻訳ツール

YouTube ライブチャットをリアルタイム翻訳して OBS ブラウザソースにオーバーレイ表示するツール。

## 機能

- **pytchat** で YouTube ライブチャットをリアルタイム取得
- **langdetect** でオフライン言語判定（日本語はスキップ）
- **LibreTranslate**（セルフホスト）で翻訳
- **Flask** で OBS ブラウザソース用オーバーレイを配信

## 構成

```
YouTube Live Chat
    ↓ pytchat
langdetect（オフライン言語判定）
    ↓ 日本語以外
LibreTranslate API（192.168.1.15:5000）
    ↓
Flask サーバー（localhost:7788）
    ↓
OBS ブラウザソース
```

## セットアップ

```bash
pip install pytchat langdetect requests flask
python main.py
```

## 使い方

1. `start.bat` を実行（または `python main.py`）
2. OBS のブラウザソースに `http://localhost:7788/` を設定
3. ブラウザで `http://localhost:7788/start/<video_id>` にアクセスしてチャット開始
4. 停止: `http://localhost:7788/stop`

## エンドポイント

| URL | 説明 |
|-----|------|
| `GET /` | OBS オーバーレイ HTML |
| `GET /messages` | メッセージ JSON |
| `GET /start/<video_id>` | チャット取得開始 |
| `GET /stop` | 停止＋クリア |
| `GET /status` | 状態確認 |
| `GET /test` | ダミーデータ注入（UIテスト用） |
| `GET /lt_check` | LibreTranslate 疎通確認 |

## LibreTranslate セットアップ

セルフホスト（LXC 192.168.1.15:5000）を使用。
`main.py` の `LIBRETRANSLATE_URL` を環境に合わせて変更。

> 公開エンドポイント: `http://123.225.35.19:5000/translate`
> 経路: IX2215 NAPTル → Proxmox socat (192.168.1.11:5001) → LXC LibreTranslate (192.168.1.15:5000)
> ⚠️ 認証未設定（TODO: Nginx + APIキー認証）

## TODO

- [ ] LibreTranslate ネットワーク問題解決（socat 転送 or VLAN設定）
- [ ] 翻訳キュー実装（複数人同時対応、ワーカースレッド3本）
- [ ] DeepL API フォールバック対応
- [ ] Discord 認証（GRAPRO サーバー会員限定）
- [ ] Windows exe 化（PyInstaller）
- [ ] 設定 GUI（video_id 入力、翻訳エンジン切替）

## ライセンス

MIT
