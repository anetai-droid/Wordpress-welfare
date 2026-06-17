# Wordpress-welfare

福祉・介護関連ニュースを取得し、AI で記事化して WordPress に下書き投稿するバッチスクリプトです。

Google ニュース RSS から記事候補を取得し、Ollama で本文生成と品質チェックを行ったうえで、WordPress REST API に下書きとして投稿します。

## 主な構成

- `main.py` - RSS 取得、AI 生成、品質チェック、WordPress 投稿のメイン処理
- `requirements.txt` - Python 依存パッケージ
- `run_news.bat` - Windows 環境での実行補助
- `.env.example` - 環境変数のサンプル
- `docker-compose.yml` / `Dockerfile` - コンテナ実行用設定

## 処理の流れ

1. Google ニュース RSS から福祉・介護・助成金関連の記事を取得
2. SQLite の履歴 DB で重複投稿を除外
3. Ollama に記事本文の生成を依頼
4. AI で品質チェックを実行
5. WordPress REST API に下書き投稿

## ローカル実行

```bash
pip install -r requirements.txt
python main.py
```

実行前に `.env.example` を参考に `.env` を用意してください。

## 必要な環境変数

- `OLLAMA_NGROK_URL` - Ollama API の接続先
- `OLLAMA_MODEL` - 使用する Ollama モデル
- `WP_URL` - WordPress サイト URL
- `WP_USERNAME` - WordPress ユーザー名
- `WP_APP_PASSWORD` - WordPress アプリケーションパスワード

## 注意

WordPress の認証情報や Ollama 接続 URL は `.env` で管理し、リポジトリにはコミットしないでください。
