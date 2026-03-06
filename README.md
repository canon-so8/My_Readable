# My Readable

**PDF翻訳アプリ** — PDFをアップロードすると、レイアウトを保持したまま日本語に翻訳します。

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

## ✨ 特徴

- 📄 **レイアウト保持翻訳** — 図・表・数式を維持したまま本文のみ翻訳
- 📑 **複数ファイル対応** — 複数PDFを一括アップロード、直列キュー処理
- 🔄 **リアルタイム進捗表示** — SSEストリーミングでページ単位の進捗を表示
- 🧮 **数式・コード自動スキップ** — 数式記号やコードブロックを検出し翻訳をスキップ
- 📚 **参考文献セクション自動スキップ** — References 以降の翻訳を省略
- 💾 **翻訳キャッシュ** — 同じテキストの再翻訳を回避

## 🛠️ 技術スタック

| レイヤー | 技術 |
|---|---|
| フロントエンド | HTML / CSS / JavaScript（フレームワーク不使用） |
| バックエンド | Python / FastAPI / Uvicorn |
| PDF処理 | PyMuPDF (fitz) |
| 翻訳 | Google Translate (deep-translator) |
| フォント | IPAexゴシック |

## 🚀 セットアップ

### 前提条件

- Python 3.10 以上

### インストール

```bash
git clone https://github.com/canon-so8/My_Readable.git
cd My_Readable
pip install -r requirements.txt
```

### 起動

```bash
uvicorn server:app --reload --port 8000
```

ブラウザで http://localhost:8000 を開いてください。

## 📁 プロジェクト構成

```
My_Readable/
├── server.py              # FastAPIサーバー（メインロジック）
├── requirements.txt       # Python依存パッケージ
├── vercel.json            # Vercelデプロイ設定
├── api/
│   └── translate.py       # Vercel Serverless Function用エンドポイント
├── public/
│   ├── index.html         # フロントエンド
│   ├── app.js             # クライアントサイドロジック
│   └── styles.css         # スタイルシート
├── fonts/
│   └── ipaexg.ttf         # IPAexゴシックフォント
└── docs/                  # 開発ドキュメント
```

## 📝 使い方

1. アプリを起動し、ブラウザでアクセス
2. PDFファイルをドラッグ＆ドロップ、またはファイル選択（複数可、最大100MB/ファイル）
3. 翻訳完了後、ダウンロードボタンで翻訳済みPDFを取得


## 📄 ライセンス

MIT License

### フォントについて

本プロジェクトに含まれる IPAexゴシック (ipaexg.ttf) は [IPAフォントライセンスv1.0](https://moji.or.jp/ipafont/license/) に基づき配布しています。
