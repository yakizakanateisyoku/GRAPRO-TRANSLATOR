# GRAPRO-TRANSLATOR

ライブ配信（YouTube / Twitch / ツイキャス）のチャットをリアルタイム翻訳して
OBS ブラウザソースにオーバーレイ表示するツール。

## 機能

- YouTube / Twitch / ツイキャスのライブチャットをリアルタイム取得
- GRAPRO 中継サーバー（Azure Translator）による高精度翻訳・言語自動検出
- LibreTranslate への自動フォールバック
- Flask で OBS ブラウザソース用オーバーレイを配信（色・フォント等カスタマイズ可）
- GUI（customtkinter）でかんたん操作
- 棒読みちゃん連携（Socket 読み上げ）
- SHOWROOM 盛り上がり度数表示
- 開発者モード（🔬 / Ctrl+Shift+D）で不具合調査用ログを保存

## クイックスタート

### exe版（推奨）

1. [Releases](https://github.com/yakizakanateisyoku/GRAPRO-TRANSLATOR/releases) から `grapro-translator-vX.X.X.exe` をダウンロード
2. exe を実行
3. 配信URL（YouTube / Twitch / ツイキャス）を入力して「開始」
4. OBS のブラウザソースに `http://localhost:7788/` を設定

### Python版

```bash
pip install -r requirements.txt
python gui.py
```

## 設定（config.json）

初回起動時に exe と同じフォルダへ自動生成されます。通常は変更不要です。
（雛形: `config.example.json`）

| 設定 | 説明 |
|------|------|
| `worker_id` | クライアント識別ID（自動発行・変更不要） |
| `my_channels` | よく使う配信チャンネルURL（GUIの設定から登録） |
| `bouyomi_enabled` / `bouyomi_port` | 棒読みちゃん連携 |
| `developer_mode` | 不具合調査用ログの保存（通常は false） |

## OBS設定

1. ソース → ＋ → ブラウザ
2. URL: `http://localhost:7788/`
3. 幅: 任意 / 高さ: 任意
4. 「カスタムCSS」は空にする

## セキュリティ

状態変更系のローカルAPI（`/start` `/stop` `/settings` 等）は起動ごとに生成される
トークンで保護されています。GUI・オーバーレイは自動で付与するため通常は意識不要です。
ブラウザから手動で叩く場合は起動コンソールに表示される `?token=XXX` を付けてください。

## コミュニティ

質問・バグ報告・要望は Discord サーバーへ:
https://discord.gg/tR7DgQNJRz

## ライセンス

MIT
