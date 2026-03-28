# OBS YouTube Live Chat Translator

YouTube ライブチャットをリアルタイム翻訳して OBS ブラウザソースにオーバーレイ表示するツール。

## 機能

- pytchat で YouTube ライブチャットをリアルタイム取得
- langdetect でオフライン言語判定（日本語はスキップ）
- 翻訳エンジン: DeepL / Azure Translator / LibreTranslate 対応
- Flask で OBS ブラウザソース用オーバーレイを配信
- GUI（customtkinter）でかんたん操作
- 翻訳失敗メッセージの再翻訳（更新ボタン）

## クイックスタート

### exe版（推奨）

1. [Releases](https://github.com/yakizakanateisyoku/live-chat-translator/releases) から `obs-translator.exe` をダウンロード
2. exe を実行
3. YouTube の動画URLまたはVideo IDを入力して「開始」
4. OBS のブラウザソースに `http://localhost:7788/` を設定

### Python版

```bash
pip install -r requirements.txt
python gui.py
```

## 設定（config.json）

初回起動時に自動生成されます。翻訳エンジンの設定を変更する場合は直接編集してください。

| 設定 | 説明 | デフォルト |
|------|------|-----------|
| `translate_engine` | 翻訳エンジン (`deepl` / `azure` / `libretranslate`) | `deepl` |
| `deepl_api_key` | DeepL API キー | (空) |
| `azure_api_key` | Azure Translator API キー | (空) |
| `azure_region` | Azure リージョン | `japaneast` |
| `translate_url` | LibreTranslate URL | `https://lt.f1234k.com/translate` |
| `overlay_port` | オーバーレイのポート番号 | `7788` |
| `target_lang` | 翻訳先言語 | `ja` |
| `max_messages` | 表示メッセージ数上限 | `20` |
| `num_workers` | 翻訳ワーカースレッド数 | `3` |

## OBS設定

1. ソース → ＋ → ブラウザ
2. URL: `http://localhost:7788/`
3. 幅: 任意 / 高さ: 任意
4. 「カスタムCSS」は空にする

## コミュニティ

質問・バグ報告・要望は Discord サーバーへ:
https://discord.gg/tR7DgQNJRz

## ライセンス

MIT
