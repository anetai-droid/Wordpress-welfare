# ── ベースイメージ ──────────────────────────────────────────
FROM python:3.11-slim

# ── 作業ディレクトリ ────────────────────────────────────────
WORKDIR /app

# Pythonの標準出力バッファリングを無効化（Docker上でリアルタイム表示するため必須）
ENV PYTHONUNBUFFERED=1

# ── 依存ライブラリのインストール（最小限・安全なパッケージのみ）──
#    requirements.txt を先にコピーすることでレイヤーキャッシュを活用
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── アプリケーションコードをコピー ──────────────────────────
#    .dockerignore により .env / output/ / .git/ 等は除外される
COPY . .

# ── 非rootユーザーで実行（セキュリティ強化）───────────────────
RUN useradd --no-create-home appuser
USER appuser

# ── 実行コマンド ────────────────────────────────────────────
CMD ["python", "main.py"]
